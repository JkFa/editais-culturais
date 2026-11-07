import json
from collections import Counter
from datetime import UTC, datetime

import httpx
import pytest
from conftest import carregar

from worker.coletores.mapas_culturais import Instancia, Parametros, coletar_instancia, converter
from worker.http import ClienteHttp, ErroColeta, user_agent

AGORA = datetime(2026, 11, 6, 18, 0, tzinfo=UTC)
PARAMS = Parametros(
    tipos_aceitos=["Edital"],
    excluir_titulo_regex=[r"\bteste\b", r"conselho (municipal|estadual) de politica cultural"],
)
CE = Instancia(
    slug="ce",
    nome="CE",
    base_url="https://mapacultural.secult.ce.gov.br",
    abrangencia="estadual",
    uf="CE",
)
BR = Instancia(slug="br", nome="Brasil", base_url="https://mapa.cultura.gov.br")


def test_converte_item_real_do_ceara():
    item = next(i for i in carregar("mapas_mapacultural.secult.ce.gov.br.json") if i["id"] == 8473)
    bruto, texto = converter(item, CE, PARAMS, AGORA, Counter())
    assert bruto.fonte == "mapas:ce" and bruto.id_externo == "8473"
    assert bruto.url_origem == "https://mapacultural.secult.ce.gov.br/oportunidade/8473/"
    assert bruto.fim_tem_horario is True  # 22:00 em Fortaleza
    assert bruto.inscricao_fim == datetime(2026, 11, 13, 1, 0, tzinfo=UTC)
    assert bruto.uf == "CE" and bruto.abrangencia == "estadual"
    assert bruto.titulo in texto


def test_pdf_vem_dos_arquivos_de_regras_ou_downloads():
    item = next(i for i in carregar("mapas_mapa.cultura.gov.br.json") if i["id"] == 12667)
    bruto, _ = converter(item, BR, PARAMS, AGORA, Counter())
    assert bruto.url_edital_pdf.endswith("sei-3193023-edital-minc-mec-n-13.pdf")


def test_orgao_usa_entidade_responsavel_ou_agente():
    item = next(i for i in carregar("mapas_mapa.cultura.gov.br.json") if i["id"] == 12667)
    bruto, _ = converter(item, BR, PARAMS, AGORA, Counter())
    assert "SEFLI" in bruto.orgao_fomento


def test_descartes_nas_fixtures_reais():
    descartes: Counter = Counter()
    itens = carregar("mapas_mapa.cultura.gov.br.json")
    validos = [i for i in itens if converter(i, BR, PARAMS, AGORA, descartes)]
    assert len(validos) == 3
    assert descartes["fluxo_continuo_sem_prazo"] == 4  # fim em 2100/2111
    assert descartes["titulo_excluido"] == 2  # "TESTE EDITAL ..."
    assert descartes["tipo:Curso"] == 6


def test_encerrado_e_ainda_nao_aberto_sao_descartados():
    item = {
        "id": 1,
        "name": "Edital Teste A real",
        "type": {"name": "Edital"},
        "registrationFrom": {"date": "2026-01-01 00:00:00", "timezone": "America/Sao_Paulo"},
        "registrationTo": {"date": "2026-10-01 23:59:00", "timezone": "America/Sao_Paulo"},
        "owner": {"name": "Fulano"},
    }
    d: Counter = Counter()
    assert converter({**item, "name": "Edital Fictício A"}, BR, PARAMS, AGORA, d) is None
    assert d["encerrado"] == 1
    futuro = {
        **item,
        "name": "Edital Fictício B",
        "registrationFrom": {"date": "2026-12-01 00:00:00", "timezone": "America/Sao_Paulo"},
        "registrationTo": {"date": "2027-01-01 23:59:00", "timezone": "America/Sao_Paulo"},
    }
    assert converter(futuro, BR, PARAMS, AGORA, d) is None and d["ainda_nao_abriu"] == 1


def servidor(paginas: list[list[dict]], capturadas: list | None = None):
    def tratar(req: httpx.Request) -> httpx.Response:
        if capturadas is not None:
            capturadas.append(req)
        pagina = int(req.url.params["@page"])
        return httpx.Response(200, json=paginas[pagina - 1] if pagina <= len(paginas) else [])

    return httpx.MockTransport(tratar)


async def test_coleta_pagina_ate_acabar_e_envia_filtros():
    itens = carregar("mapas_mapacultural.secult.ce.gov.br.json")
    capturadas: list[httpx.Request] = []
    # Duas páginas "cheias" são simuladas repetindo itens com ids novos.
    pag1 = [{**itens[0], "id": 90000 + n} for n in range(100)]
    pag2 = [itens[1]]
    async with ClienteHttp(transporte=servidor([pag1, pag2], capturadas), pausa_base=0) as c:
        r = await coletar_instancia(c, CE, PARAMS, AGORA)
    assert len(capturadas) == 2
    q = capturadas[0].url.params
    assert q["@limit"] == "100" and q["registrationTo"].startswith("GTE(") and "status" in q
    assert "registrationFrom" in q and "longDescription" in q["@select"]
    assert len(r.brutos) == 101


async def test_resposta_que_nao_e_lista_falha():
    t = httpx.MockTransport(lambda req: httpx.Response(200, json={"erro": "x"}))
    async with ClienteHttp(transporte=t, pausa_base=0) as c:
        with pytest.raises(ErroColeta):
            await coletar_instancia(c, CE, PARAMS, AGORA)


async def test_retry_respeita_retry_after_e_depois_funciona():
    chamadas = {"n": 0}

    def tratar(req):
        chamadas["n"] += 1
        if chamadas["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=[])

    async with ClienteHttp(transporte=httpx.MockTransport(tratar), pausa_base=0) as c:
        assert await c.json("https://x.invalid/api") == []
    assert chamadas["n"] == 3


async def test_falha_definitiva_apos_tres_tentativas():
    chamadas = {"n": 0}

    def tratar(req):
        chamadas["n"] += 1
        return httpx.Response(503)

    async with ClienteHttp(transporte=httpx.MockTransport(tratar), pausa_base=0) as c:
        with pytest.raises(ErroColeta, match="3 tentativas"):
            await c.json("https://x.invalid/api")
    assert chamadas["n"] == 3


async def test_erro_4xx_nao_repete():
    chamadas = {"n": 0}

    def tratar(req):
        chamadas["n"] += 1
        return httpx.Response(403)

    async with ClienteHttp(transporte=httpx.MockTransport(tratar), pausa_base=0) as c:
        with pytest.raises(ErroColeta, match="403"):
            await c.json("https://x.invalid/api")
    assert chamadas["n"] == 1


async def test_user_agent_identificavel_sem_email(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/editais")
    assert user_agent() == "editais-culturais-bot/1.0 (+https://github.com/org/editais)"
    assert "@" not in user_agent()
    monkeypatch.delenv("GITHUB_REPOSITORY")
    monkeypatch.delenv("REPO_URL", raising=False)
    assert user_agent() == "editais-culturais-bot/1.0"


def test_fixtures_sao_json_valido():
    for nome in ("mapas_mapa.cultura.gov.br.json", "mapas_mapacultural.pb.gov.br.json"):
        assert isinstance(
            json.loads(
                (__import__("pathlib").Path(__file__).parent / "fixtures" / nome).read_text()
            ),
            list,
        )
