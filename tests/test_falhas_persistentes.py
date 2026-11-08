"""ConnectTimeout persistente vira `bloqueada_em_ci` (Issue semanal de conferência manual); falha curta continua `erro`."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx

from worker import bloqueadas
from worker.catalogo import classificar_falha, eh_falha_de_conexao
from worker.coletores.mapas_culturais import Instancia
from worker.scraper_worker import status_das_instancias

AGORA = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
CT = (
    "falha após 3 tentativas em https://mapacultural.pe.gov.br/api/opportunity/find: ConnectTimeout"
)


def antes(dias, erro=CT, status="erro"):
    t = (AGORA - timedelta(days=dias)).isoformat()
    return {"status": status, "erro": erro, "ultima_execucao": t, "ultimo_sucesso": t}


def test_primeira_falha_de_conexao_e_so_erro_e_marca_o_inicio():
    st, texto = classificar_falha(CT, {"status": "ok", "ultima_execucao": AGORA.isoformat()}, AGORA)
    assert st == "erro" and texto.endswith("[desde 2026-11-08]")


def test_persistente_por_tres_dias_vira_bloqueada_em_ci_e_mantem_a_data_de_inicio():
    ontem = classificar_falha(CT, antes(1), AGORA)
    assert ontem[0] == "erro" and "[desde 2026-11-07]" in ontem[1], (
        "registro antigo sem marca: usa a última execução"
    )
    st, texto = classificar_falha(CT, {"status": "erro", "erro": CT + " [desde 2026-11-05]"}, AGORA)
    assert (
        st == "bloqueada_em_ci"
        and texto.count("[desde") == 1
        and texto.endswith("[desde 2026-11-05]")
    )
    st2, _ = classificar_falha(
        CT, {"status": "bloqueada_em_ci", "erro": CT + " [desde 2026-11-05]"}, AGORA
    )
    assert st2 == "bloqueada_em_ci", "continua bloqueada enquanto persistir"


def test_um_dia_de_sucesso_zera_a_contagem():
    # ontem deu certo (status ok): a falha de hoje recomeça a sequência
    st, texto = classificar_falha(
        CT,
        {"status": "ok", "erro": None, "ultima_execucao": (AGORA - timedelta(days=1)).isoformat()},
        AGORA,
    )
    assert st == "erro" and "[desde 2026-11-08]" in texto


def test_outros_erros_nao_viram_bloqueio_e_403_e_imediato():
    assert classificar_falha("HTTP 500 em https://x", antes(9, "HTTP 500"), AGORA)[0] == "erro"
    assert (
        classificar_falha(
            "ConnectError [SSL: CERTIFICATE_VERIFY_FAILED]", antes(9, "ConnectError"), AGORA
        )[0]
        == "erro"
    ), "certificado quebrado é problema do site"
    assert classificar_falha("JSONDecodeError", None, AGORA)[0] == "erro"
    assert classificar_falha("HTTP 403 em www.sescpr.com.br", None, AGORA)[0] == "bloqueada_em_ci"
    assert classificar_falha("HTTP 429 em x", None, AGORA)[0] == "bloqueada_em_ci"
    assert classificar_falha("", None, AGORA) == ("erro", None)
    assert (
        eh_falha_de_conexao(CT)
        and not eh_falha_de_conexao("HTTP 500")
        and not eh_falha_de_conexao(None)
    )


def test_status_das_instancias_aplica_a_regra_so_a_quem_falhou():
    pe = Instancia(
        slug="pe",
        nome="PE",
        base_url="https://mapacultural.pe.gov.br",
        abrangencia="estadual",
        uf="PE",
    )
    ce = Instancia(
        slug="ce",
        nome="CE",
        base_url="https://mapacultural.secult.ce.gov.br",
        abrangencia="estadual",
        uf="CE",
    )
    inativa = Instancia(
        slug="df", nome="DF", base_url="https://mapa.cultura.df.gov.br", ativa=False, motivo="403"
    )
    ant = {
        "mapas:pe": {
            "status": "erro",
            "erro": CT + " [desde 2026-11-04]",
            "ultimo_sucesso": "2026-11-03T10:00:00Z",
        }
    }
    saida = {
        s.fonte: s
        for s in status_das_instancias(
            [pe, ce, inativa], {"mapas:ce"}, {"mapas:pe": CT}, {"mapas:ce": 3}, ant, AGORA
        )
    }
    assert (
        saida["mapas:pe"].status == "bloqueada_em_ci"
        and "[desde 2026-11-04]" in saida["mapas:pe"].erro
    )
    assert saida["mapas:pe"].ultimo_sucesso.isoformat().startswith("2026-11-03")
    assert saida["mapas:ce"].status == "ok" and saida["mapas:df"].status == "desativada"
    # no dia seguinte à primeira falha continua sendo apenas `erro`
    novo = status_das_instancias(
        [pe], set(), {"mapas:pe": CT}, {}, {"mapas:pe": antes(0, CT + " [desde 2026-11-08]")}, AGORA
    )[0]
    assert novo.status == "erro"


def test_issue_semanal_explica_que_pode_ser_filtro_de_ip_e_nao_duplica(monkeypatch, tmp_path):
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"fontes": [
        {"fonte": "mapas:pe", "nome": "Mapa Cultural de Pernambuco", "url": "https://mapacultural.pe.gov.br", "status": "bloqueada_em_ci", "erro": CT + " [desde 2026-11-04]"},
        {"fonte": "vigia-sesc-pr-editais", "nome": "Sesc Paraná: editais", "url": "https://www.sescpr.com.br/editais/", "status": "bloqueada_em_ci", "erro": "HTTP 403 em www.sescpr.com.br"},
        {"fonte": "mapas:ce", "nome": "CE", "url": "https://x", "status": "ok", "erro": None},
    ]}), encoding="utf-8")  # fmt: skip
    criadas = []

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200, json=[{"title": "Conferir manualmente: Sesc Paraná: editais"}]
            )
        if req.url.path.endswith("/issues"):
            criadas.append(json.loads(req.content))
        return httpx.Response(201, json={})

    monkeypatch.setattr(bloqueadas, "SNAPSHOT", snap)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    real = httpx.AsyncClient
    monkeypatch.setattr(
        bloqueadas.httpx,
        "AsyncClient",
        lambda **kw: real(**kw, transport=httpx.MockTransport(tratar)),
    )
    assert asyncio.run(bloqueadas.principal()) == 0
    assert len(criadas) == 1, "o Sesc já tinha Issue aberta: não duplica"
    corpo = criadas[0]["body"]
    assert criadas[0]["title"] == "Conferir manualmente: Mapa Cultural de Pernambuco"
    assert (
        "não respondeu aos servidores do GitHub" in corpo
        and "filtro de IP" in corpo
        and "contornar" in corpo
    )
