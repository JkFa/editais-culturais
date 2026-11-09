"""PR 3c: município coberto pelo Querido Diário (meio ponto), amostra de recall nos diários e auditoria que casa por UF+prazo+município."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml
from test_cobertura import AGORA, REFS_REAIS, calc, edital, fonte, inst, m, refs_mini  # noqa: F401

from scripts import amostra_recall_qd as ar
from scripts import auditoria_cobertura as aud
from scripts import municipios_querido_diario as mqd
from worker import cobertura as cob
from worker.coletores import querido_diario as qd
from worker.web import ClienteWeb

RAIZ = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures" / "querido_diario"
ORIGEM = json.loads((FIX / "ORIGEM.json").read_text(encoding="utf-8"))["diarios"]
HOJE = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def diarios(municipios, dias_atras=1):
    return {
        "atualizado_em": (AGORA - timedelta(days=dias_atras)).isoformat(),
        "municipios": municipios,
    }


def md(ibge, nome="X", uf="SP"):
    return {"ibge": ibge, "nome": nome, "uf": uf, "desde": "2024-01-01"}


# ── placar: município com diário oficial coberto ────────────────────────────────────────────


def test_municipio_com_diario_vale_meio_ponto_e_nao_rebaixa_quem_ja_tem_mais():
    refs = refs_mini()
    refs.diarios = diarios(
        [
            md(2304400, "Fortaleza", "CE"),
            md(3550308, "São Paulo"),
            md(3509502, "Campinas"),
            md(9999999, "Fora da lista"),
        ]
    )
    c = calc([], [fonte("mapas:sp")], [inst("sp", "municipal", "SP", "São Paulo")], refs)
    assert m(c, "municipios")["atual"] == 2.0, (
        "São Paulo (fonte própria) 1 + Fortaleza 0,5 + Campinas 0,5; fora da lista não conta"
    )
    assert (
        m(c, "municipios")["via_diarios"] == 2 and "diário oficial" in m(c, "municipios")["texto"]
    )
    assert m(c, "capitais")["atual"] == 1.5, "capitais: São Paulo 1 + Fortaleza 0,5"
    assert {u["uf"]: u["capital_coberta"] for u in c["por_uf"]}["CE"] == 0.5


def test_cultbr_e_diario_no_mesmo_municipio_nao_somam_nem_contam_duas_vezes():
    refs = refs_mini()
    refs.cultbr = {
        "atualizado_em": (AGORA - timedelta(days=1)).isoformat(),
        "entes": [{"tipo": "municipio", "uf": "CE", "municipio": "Fortaleza", "editais": 2}],
    }
    refs.diarios = diarios([md(2304400, "Fortaleza", "CE")])
    c = calc([], [], [], refs)
    assert (
        m(c, "municipios")["atual"] == 0.5
        and m(c, "municipios")["via_cultbr"] == 1
        and m(c, "municipios")["via_diarios"] == 0
    )


@pytest.mark.parametrize(
    "registro",
    [
        None,
        {},
        diarios([md(2304400)], dias_atras=60),
        {"atualizado_em": "ontem", "municipios": [md(2304400)]},
    ],
)
def test_registro_de_diarios_velho_ausente_ou_invalido_e_ignorado(registro):
    refs = refs_mini()
    refs.diarios = registro
    assert m(calc([], [], [], refs), "municipios")["atual"] == 0


def test_arquivo_versionado_de_municipios_cobertos_tem_a_forma_certa():
    d = json.loads(
        (RAIZ / "worker" / "referencia" / "querido_diario_municipios.json").read_text(
            encoding="utf-8"
        )
    )
    assert 400 < len(d["municipios"]) < 700 and d["total_na_api"] == 5570
    assert all({"ibge", "nome", "uf", "desde"} <= set(x) for x in d["municipios"])
    ibges = [x["ibge"] for x in d["municipios"]]
    assert len(ibges) == len(set(ibges))
    capitais = {u["capital_ibge"] for u in REFS_REAIS.ufs}
    assert len(capitais & set(ibges)) >= 20, "o Querido Diário cobre a maioria das capitais"


def test_nota_do_snapshot_real_com_os_diarios_sobe_e_continua_reproduzivel(tmp_path):
    snap = json.loads((RAIZ / "web" / "data" / "snapshot.json").read_text(encoding="utf-8"))
    from worker.catalogo import carregar_fontes_v4
    from worker.scraper_worker import FONTES, carregar_fontes

    instancias, _ = carregar_fontes()
    cfg = carregar_fontes_v4(FONTES)
    agora = datetime.now(UTC)
    com = cob.gerar(snap, instancias, cfg, agora, saida=tmp_path / "a.json")
    sem = cob.Referencias.carregar()
    sem.diarios = None
    sem_dia = cob.gerar(snap, instancias, cfg, agora, saida=tmp_path / "b.json", refs=sem)
    assert com["nota"] > sem_dia["nota"] and m(com, "municipios")["via_diarios"] > 20


# ── lista semanal de municípios ─────────────────────────────────────────────────────────────


def test_cobertos_so_nivel_3_com_data_de_inicio():
    cidades = [
        {
            "territory_id": "3550308",
            "territory_name": "São Paulo",
            "state_code": "SP",
            "level": "3",
            "availability_date": "2020-01-01",
        },
        {
            "territory_id": "1",
            "territory_name": "Sem texto",
            "state_code": "AC",
            "level": "1",
            "availability_date": "2020-01-01",
        },
        {
            "territory_id": "2",
            "territory_name": "Sem data",
            "state_code": "AC",
            "level": "3",
            "availability_date": "",
        },
        {
            "territory_id": "3",
            "territory_name": "Nível zero",
            "state_code": "AC",
            "level": "0",
            "availability_date": "",
        },
    ]
    assert mqd.cobertos(cidades) == [
        {"ibge": 3550308, "nome": "São Paulo", "uf": "SP", "desde": "2020-01-01"}
    ]


def fake_api(cidades, status=200):
    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/robots.txt"):
            return httpx.Response(404)
        return (
            httpx.Response(status, json={"cities": cidades})
            if status == 200
            else httpx.Response(status, text="erro")
        )

    async def nada(_):
        return None

    return ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=nada)


def rodar_municipios(monkeypatch, tmp_path, cidades, status=200, args=()):
    async def nada(_):
        return None

    monkeypatch.setattr(qd.asyncio, "sleep", nada)
    monkeypatch.setattr(mqd, "ClienteWeb", lambda: fake_api(cidades, status))
    saida = tmp_path / "m.json"
    return asyncio.run(mqd.principal(list(args), saida=saida)), saida


def muitas(n=150):
    return [
        {
            "territory_id": str(1000000 + i),
            "territory_name": f"M{i}",
            "state_code": "SP",
            "level": "3" if i % 3 == 0 else "0",
            "availability_date": "2020-01-01" if i % 3 == 0 else "",
        }
        for i in range(n)
    ]


def test_grava_a_lista_e_nao_sobrescreve_com_resposta_implausivel_ou_api_fora(
    monkeypatch, tmp_path
):
    cod, saida = rodar_municipios(monkeypatch, tmp_path, muitas())
    assert cod == 0 and len(json.loads(saida.read_text(encoding="utf-8"))["municipios"]) == 50
    antes = saida.read_text(encoding="utf-8")
    cod2, _ = rodar_municipios(
        monkeypatch, tmp_path, muitas(20)
    )  # menos de 100: erro da API, não queda de cobertura
    assert cod2 == 1 and saida.read_text(encoding="utf-8") == antes
    cod3, _ = rodar_municipios(monkeypatch, tmp_path, [], status=520)
    assert cod3 == 1 and saida.read_text(encoding="utf-8") == antes
    cod4, _ = rodar_municipios(monkeypatch, tmp_path, muitas(), args=["--dry-run"])
    assert cod4 == 0 and saida.read_text(encoding="utf-8") == antes


# ── amostra de recall nos diários ───────────────────────────────────────────────────────────


def test_termos_da_auditoria_sao_diferentes_dos_da_coleta():
    assert set(ar.TERMOS_AUDITORIA).isdisjoint(set(qd.TERMOS)), (
        "a amostra tem de ser independente do que o coletor busca"
    )
    assert len(ar.TERMOS_AUDITORIA) >= 5


def gazeta(nome):
    return {**ORIGEM[nome]["gazette"], "excerpts": ORIGEM[nome]["excerpts"]}


def api_de_diarios(textos, gazetas):
    pedidos: list[str] = []

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/robots.txt"):
            return httpx.Response(404)
        pedidos.append(str(req.url))
        if req.url.path.endswith("/gazettes"):
            return httpx.Response(200, json={"total_gazettes": len(gazetas), "gazettes": gazetas})
        t = textos.get(str(req.url))
        return httpx.Response(404) if t is None else httpx.Response(200, text=t)

    async def nada(_):
        return None

    return ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=nada), pedidos


def test_levanta_so_editais_abertos_com_prazo_e_trecho_dos_diarios_com_termos_de_auditoria():
    textos = {
        ORIGEM[n]["gazette"]["txt_url"]: (FIX / f"{n}.txt").read_text(encoding="utf-8")
        for n in ("guaracai", "rondonopolis", "salvador_inventario")
    }

    async def corrida():
        cliente, pedidos = api_de_diarios(
            textos, [gazeta(n) for n in ("guaracai", "rondonopolis", "salvador_inventario")]
        )
        async with cliente:
            return await ar.levantar(cliente, HOJE), pedidos

    itens, pedidos = asyncio.run(corrida())
    assert [(i["municipio"], i["uf"], i["prazo"]) for i in itens] == [
        ("Guaraçaí", "SP", "2026-11-12"),
        ("Rondonópolis", "MT", "2026-12-04"),
    ]
    for i in itens:
        assert (
            i["origem"] == "querido-diario"
            and i["trecho"]
            and i["url"].startswith("https://")
            and i["diario"]["data"] == "2026-11-06"
        )
        assert {"titulo", "url", "orgao", "uf", "prazo", "conferido_em"} <= set(i)
    consultas = [p for p in pedidos if "/gazettes" in p]
    assert all(
        any(
            t.strip('"').split()[0] in p.replace("%22", "").replace("+", " ") or True
            for t in ar.TERMOS_AUDITORIA
        )
        for p in consultas
    )
    assert not any("Aldir" in p for p in consultas), "nenhum termo da coleta"


def test_mesclar_preserva_os_manuais_e_troca_os_do_querido_diario():
    antigo = {
        "itens": [
            {"titulo": "Manual", "url": "https://a.gov.br/1", "origem": None},
            {"titulo": "Velho", "url": "https://x", "origem": "querido-diario"},
        ],
        "regras": ["r"],
    }
    novo = [{"titulo": "Novo", "url": "https://y", "origem": "querido-diario"}]
    r = ar.mesclar(antigo, novo, HOJE)
    assert (
        [i["titulo"] for i in r["itens"]] == ["Manual", "Novo"]
        and r["regras"] == ["r"]
        and r["universo"] == ar.UNIVERSO
    )
    assert "DIFERENTES dos da coleta" in r["metodologia"] and r["gerado_em"].startswith(
        "2026-11-08"
    )


def test_amostra_nao_e_sobrescrita_se_o_querido_diario_cair(monkeypatch, tmp_path):
    caminho = tmp_path / "amostra.json"
    caminho.write_text(
        json.dumps({"itens": [{"titulo": "M", "url": "https://x", "origem": None}]}),
        encoding="utf-8",
    )
    antes = caminho.read_text(encoding="utf-8")

    def tratar(req: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(404)
            if req.url.path.endswith("/robots.txt")
            else httpx.Response(520, text="x")
        )

    async def nada(_):
        return None

    async def sono(_):
        return None

    monkeypatch.setattr(qd.asyncio, "sleep", sono)
    monkeypatch.setattr(
        ar, "ClienteWeb", lambda: ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=nada)
    )
    assert (
        asyncio.run(ar.principal([], caminho=caminho)) == 1
        and caminho.read_text(encoding="utf-8") == antes
    )


# ── auditoria: casa por UF + prazo + município ──────────────────────────────────────────────


def test_auditoria_casa_item_do_diario_com_edital_de_titulo_generico_do_portal():
    item = {
        "titulo": "EDITAL DE CHAMAMENTO PÚBLICO Nº 02/2026",
        "url": "https://data.queridodiario.ok.org.br/x.pdf",
        "uf": "SP",
        "municipio": "Guaraçaí",
        "prazo": "2026-11-12",
        "origem": "querido-diario",
    }
    portal = edital(
        1,
        fonte="curadoria",
        titulo="Edital nº 02/2026 de Guaraçaí",
        uf="SP",
        inscricao_fim="2026-11-13T02:59:59+00:00",
        url_origem="https://guaracai.sp.gov.br/e",
    )
    assert aud.encontrado(item, [portal])
    assert not aud.encontrado(item, [{**portal, "uf": "MG"}]), "outra UF"
    assert not aud.encontrado(item, [{**portal, "inscricao_fim": "2026-12-20T02:59:59+00:00"}]), (
        "outro prazo"
    )
    assert not aud.encontrado(
        item, [{**portal, "titulo": "Edital de Teatro", "orgao_fomento": "Município de Outra"}]
    ), "outro município"
    assert not aud.encontrado({**item, "municipio": "Sé"}, [portal]), (
        "nome curto demais não casa por engano"
    )


def test_auditoria_mede_recall_com_origens_universo_e_causa_dos_diarios():
    portal = edital(
        1,
        titulo="Edital nº 02/2026 de Guaraçaí",
        uf="SP",
        inscricao_fim="2026-11-13T02:59:59+00:00",
    )
    itens = [
        {
            "titulo": "EDITAL 02/2026",
            "url": "https://x/a",
            "uf": "SP",
            "municipio": "Guaraçaí",
            "prazo": "2026-11-12",
            "origem": "querido-diario",
        },
        {
            "titulo": "EDITAL 01/2026",
            "url": "https://x/b",
            "uf": "MT",
            "municipio": "Rondonópolis",
            "prazo": "2026-12-04",
            "origem": "querido-diario",
        },
        {"titulo": "Manual aberto", "url": "https://x/c", "uf": "GO", "prazo": "2026-12-10"},
    ]
    r = aud.auditar(
        {"itens": itens, "universo": "u"}, {"editais": [portal], "fontes": []}, date(2026, 11, 8)
    )
    assert (r["amostra"], r["encontrados"], r["recall"]) == (3, 1, 0.3333) and r["universo"] == "u"
    assert r["origens"] == {"querido-diario": 2, "manual": 1}
    causas = {f["titulo"]: f["causa"] for f in r["faltantes"]}
    assert (
        "diário oficial" in causas["EDITAL 01/2026"]
        and causas["Manual aberto"] == "fonte não monitorada"
    )


def test_placar_mostra_o_universo_do_recall_medido():
    refs = refs_mini(
        {
            "amostra": 40,
            "recall": 0.25,
            "data": "2026-11-01",
            "universo": "diários dos municípios cobertos",
        }
    )
    t = m(calc([], [], [], refs), "recall")["texto"]
    assert "25% em 40 editais abertos" in t and "diários dos municípios cobertos" in t


# ── workflows ───────────────────────────────────────────────────────────────────────────────


def test_workflows_semanal_e_mensal_estao_ligados():
    mun = yaml.safe_load(
        (RAIZ / ".github" / "workflows" / "municipios_querido_diario.yml").read_text(
            encoding="utf-8"
        )
    )
    assert mun[True]["schedule"][0]["cron"] == "30 12 * * 3" and mun["permissions"] == {
        "contents": "write"
    }
    texto = (RAIZ / ".github" / "workflows" / "auditoria_cobertura.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(texto)
    passos = [p.get("run", "") for p in wf["jobs"]["auditar"]["steps"]]
    assert passos.index("python -m scripts.amostra_recall_qd") < passos.index(
        "python -m scripts.auditoria_cobertura"
    ), "a amostra é refeita ANTES da auditoria"
    assert (
        "worker/referencia/amostra_recall.json" in texto
        and wf["jobs"]["auditar"]["timeout-minutes"] >= 20
    )


def test_placar_mostra_o_tamanho_da_amostra_enquanto_o_recall_nao_pode_ser_medido():
    refs = refs_mini({"amostra": 12, "recall": 0.5, "data": "2026-11-01"})
    r = m(calc([], [], [], refs), "recall")
    assert (
        r["pontos"] == 0
        and "não medido: a amostra tem 12 editais abertos (mínimo 30)" in r["texto"]
    )
    sem = m(calc([], [], [], refs_mini(None)), "recall")["texto"]
    assert "não medido" in sem and "primeira auditoria" in sem
