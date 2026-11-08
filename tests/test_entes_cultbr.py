"""Registro semanal dos entes do CultBR (scripts/entes_cultbr.py) e a higiene dos candidatos do CultBR."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml

from scripts import entes_cultbr as ec
from worker.coletores import cultbr
from worker.fila import Candidato
from worker.web import ClienteWeb

RAIZ = Path(__file__).resolve().parent.parent
AGORA = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def item(ente, uf, mun, status="Aberto", ano="2025"):
    return {
        "nome_ente_recebedor": ente,
        "uf_ente_recebedor_plano_acao": uf,
        "nome_municipio_ente_recebedor_plano_acao": mun,
        "status": status,
        "ano_par": ano,
    }


def test_tipo_do_ente():
    assert ec.tipo_do_ente("MUNICIPIO DE PELOTAS") == "municipio"
    assert (
        ec.tipo_do_ente("ESTADO DO PARANA") == "estado"
        and ec.tipo_do_ente("DISTRITO FEDERAL") == "estado"
    )
    assert ec.tipo_do_ente("CONSORCIO X") is None and ec.tipo_do_ente("") is None


def test_agregar_conta_editais_abertos_e_ano_por_ente_e_ignora_dados_incompletos():
    itens = [
        item("MUNICIPIO DE JANDIRA", "SP", "JANDIRA"), item("MUNICIPIO DE JANDIRA", "SP", "JANDIRA", "Fechado", "2026"),
        item("ESTADO DO PARANA", "PR", ""), item("ESTADO DO PARANA", "PR", "Curitiba"),
        item("MUNICIPIO DE X", "SP", ""),  # município sem nome: não dá para casar com o IBGE
        item("MUNICIPIO DE Y", "", "Y"),  # sem UF
        item("CONSORCIO Z", "SP", "Z"),
    ]  # fmt: skip
    assert ec.agregar(itens) == [
        {
            "tipo": "estado",
            "uf": "PR",
            "municipio": None,
            "editais": 2,
            "abertos": 2,
            "ano_par": 2025,
        },
        {
            "tipo": "municipio",
            "uf": "SP",
            "municipio": "JANDIRA",
            "editais": 2,
            "abertos": 1,
            "ano_par": 2026,
        },
    ]


def api_falsa(paginas, falhar_pagina=None, falhas=0):
    chamadas = []

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/robots.txt"):
            return httpx.Response(404)
        pagina = int(req.url.params["page"])
        chamadas.append(pagina)
        if pagina == falhar_pagina and chamadas.count(pagina) <= falhas:
            return httpx.Response(500)
        return httpx.Response(200, json={"total_pages": len(paginas), "items": paginas[pagina - 1]})

    async def sem_espera(_):
        return None

    return ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera), chamadas


def test_baixar_todos_percorre_as_paginas_e_tenta_de_novo_pagina_instavel(monkeypatch):
    async def sem_dormir(_):
        return None

    monkeypatch.setattr(ec.asyncio, "sleep", sem_dormir)
    paginas = [
        [item("MUNICIPIO DE A", "SP", "A")],
        [item("MUNICIPIO DE B", "SP", "B")],
        [item("MUNICIPIO DE C", "RJ", "C")],
    ]
    cliente, chamadas = api_falsa(paginas, falhar_pagina=2, falhas=2)

    async def rodar():
        async with cliente:
            return await ec.baixar_todos(cliente)

    itens = asyncio.run(rodar())
    assert [i["nome_municipio_ente_recebedor_plano_acao"] for i in itens] == ["A", "B", "C"]
    assert chamadas.count(2) >= 3, "a página instável foi repetida"


def test_principal_grava_o_arquivo_e_nao_sobrescreve_quando_a_lista_fica_incompleta(
    tmp_path, monkeypatch
):
    async def sem_dormir(_):
        return None

    monkeypatch.setattr(ec.asyncio, "sleep", sem_dormir)
    saida = tmp_path / "cultbr_entes.json"
    ok, _ = api_falsa([[item("MUNICIPIO DE A", "SP", "A"), item("ESTADO DE X", "BA", "")]])
    monkeypatch.setattr(ec, "ClienteWeb", lambda: ok)
    assert asyncio.run(ec.principal([], saida=saida)) == 0
    dados = json.loads(saida.read_text(encoding="utf-8"))
    assert dados["total_registros"] == 2 and [e["tipo"] for e in dados["entes"]] == [
        "estado",
        "municipio",
    ]
    antes = saida.read_text(encoding="utf-8")
    quebrada, _ = api_falsa([[item("MUNICIPIO DE B", "SP", "B")]] * 2, falhar_pagina=2, falhas=99)
    monkeypatch.setattr(ec, "ClienteWeb", lambda: quebrada)
    assert asyncio.run(ec.principal([], saida=saida)) == 1
    assert saida.read_text(encoding="utf-8") == antes, (
        "dado parcial subestimaria a cobertura: arquivo anterior preservado"
    )
    seco, _ = api_falsa([[item("MUNICIPIO DE C", "SP", "C")]])
    monkeypatch.setattr(ec, "ClienteWeb", lambda: seco)
    assert (
        asyncio.run(ec.principal(["--dry-run"], saida=tmp_path / "x.json")) == 0
        and not (tmp_path / "x.json").exists()
    )


def test_candidato_do_cultbr_com_prazo_vencido_na_pagina_nao_vira_issue(monkeypatch):
    vencido = Candidato(
        chave="c1",
        titulo="Edital vencido",
        link_oficial="https://x.gov.br/a",
        origem="o",
        campos={"inscricao_fim": (AGORA - timedelta(days=30)).date().isoformat()},
    )
    aberto = Candidato(
        chave="c2",
        titulo="Edital aberto",
        link_oficial="https://x.gov.br/b",
        origem="o",
        campos={"inscricao_fim": "2099-01-01"},
    )
    sem_prazo = Candidato(
        chave="c3",
        titulo="Sem prazo lido",
        link_oficial="https://x.gov.br/c",
        origem="o",
        campos={},
    )
    fila = {"1": vencido, "2": aberto, "3": sem_prazo}

    async def falso(cliente, link, **kw):
        return fila[link]

    monkeypatch.setattr(cultbr, "candidato_de_link", falso)
    itens = [
        {
            "id_par_edital": k,
            "link_inscricao": k,
            "titulo": f"t{k}",
            "nome_ente_recebedor": "MUNICIPIO DE A",
            "json_oportunidade_mapas": None,
        }
        for k in fila
    ]
    corpo = json.dumps({"items": itens, "total_pages": 1})

    def tratar(req):
        return (
            httpx.Response(404)
            if req.url.path.endswith("robots.txt")
            else httpx.Response(200, text=corpo, headers={"content-type": "application/json"})
        )

    async def rodar():
        async with ClienteWeb(transporte=httpx.MockTransport(tratar)) as c:
            return await cultbr.coletar(c, "https://api.test/v1/editais", 0, set(), set(), AGORA)

    res = asyncio.run(rodar())
    assert [c.chave for c in res.candidatos] == ["c2", "c3"]
    assert res.descartes["encerrado_na_pagina"] == 1


def test_workflow_semanal_so_atualiza_o_registro_de_entes():
    texto = (RAIZ / ".github" / "workflows" / "entes_cultbr.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(texto)
    assert wf[True]["schedule"][0]["cron"] == "0 12 * * 3" and "workflow_dispatch" in wf[True]
    assert (
        wf["permissions"] == {"contents": "write"} and wf["jobs"]["entes"]["timeout-minutes"] <= 15
    )
    assert (
        "python -m scripts.entes_cultbr" in texto
        and "git add worker/referencia/cultbr_entes.json" in texto
    )
    assert "secrets." not in texto.replace("secrets.GITHUB_TOKEN", "")


@pytest.mark.parametrize("nome", ["entes_cultbr.yml"])
def test_workflow_existe(nome):
    assert (RAIZ / ".github" / "workflows" / nome).exists()
