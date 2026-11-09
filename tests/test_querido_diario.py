"""Coletor de diários oficiais municipais (Querido Diário): textos reais de 06/11/2026 e uma API simulada."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from worker.catalogo import FonteConfig, carregar_fontes_v4
from worker.coletores import querido_diario as qd
from worker.fila import Candidato, marcador
from worker.modelos import Edital
from worker.scraper_worker import FONTES
from worker.web import ClienteWeb

RAIZ = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures" / "querido_diario"
ORIGEM = json.loads((FIX / "ORIGEM.json").read_text(encoding="utf-8"))["diarios"]
AGORA = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def ler(nome):
    return (FIX / f"{nome}.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def sem_espera(monkeypatch):
    async def nada(_):
        return None

    monkeypatch.setattr(qd.asyncio, "sleep", nada)


# ── etapa 1: o trecho parece abertura? ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "trecho, esperado",
    [
        ("O MUNICÍPIO DE GUARAÇAÍ torna público o presente Edital de chamamento", "abertura"),
        ("As inscrições estarão abertas de 21 de outubro a 11 de novembro", "abertura"),
        ("Período de inscrição: de 09/11/2026 a 20/11/2026", "abertura"),
        (
            "EXTRATOS DOS TERMOS DE EXECUÇÃO CULTURAL - EDITAL DE CHAMAMENTO PÚBLICO Nº 01/2026",
            "posterior",
        ),
        ("RESULTADO FINAL DO EDITAL 03/2026 - homologação das inscrições", "posterior"),
        ("Errata ao Edital de Chamamento Público nº 05/2026", "posterior"),
        (
            "FONTE 1501 FUNDO MUNICIPAL DE CULTURA LEI ALDIR BLANC 37.582-9 1.445,13 3.226,27",
            "ruido",
        ),
        ("remun - fnc - politica nac. aldir blanc 14399/22 15.000,00", "ruido"),
    ],
)
def test_classificar_trecho(trecho, esperado):
    assert qd.classificar_trecho(trecho) == esperado


# ── etapa 2: a passagem do edital, com textos reais ──────────────────────────────────────────


def test_guaracai_e_rondonopolis_tem_passagem_de_abertura_com_prazo_e_trecho_que_fala_de_inscricoes():
    g = qd.achar_passagens(ler("guaracai"), AGORA)
    assert [p.titulo for p in g] == ["EDITAL DE CHAMAMENTO PÚBLICO Nº 02/2026"]
    assert (
        g[0].prazo.fim > AGORA
        and "inscri" in g[0].prazo.trecho.lower()
        and "7.1" in g[0].prazo.trecho
    )
    r = qd.achar_passagens(ler("rondonopolis"), AGORA)
    assert [p.titulo for p in r] == ["EDITAL DE CHAMAMENTO PÚBLICO Nº 001/2026"]
    assert r[0].prazo.fim.date().isoformat() == "2026-12-05", "04/12 23h59 em Brasília"


def test_inscritos_no_cadastro_nao_e_prazo_de_inscricao():
    """Salvador: 'inscritos no cadastro da Prefeitura' casava com 'inscri'; o inventário de bens não é pedido de inscrição."""
    assert qd.achar_passagens(ler("salvador_inventario"), AGORA) == []


def test_prazo_vencido_e_texto_sem_cultura_ou_so_de_resultado_nao_geram_passagem():
    assert qd.achar_passagens(ler("guaracai"), datetime(2027, 3, 1, tzinfo=UTC)) == []
    educacao = (
        "EDITAL SME 003/2026 A Secretaria Municipal de Educação torna público o processo de matrícula, com recursos de R$ 10.000,00 para a rede. "
        "As inscrições ocorrerão de 01/11/2026 a 20/11/2026. " + "Texto do edital. " * 20
    )
    assert qd.achar_passagens(educacao, AGORA) == [], "sem cultura no texto, não é edital cultural"
    resultado = (
        "RESULTADO FINAL DO EDITAL DE CHAMAMENTO PÚBLICO Nº 03/2026 cultura. Inscrições de 01/11/2026 a 20/11/2026. R$ 5.000,00 "
        + "x " * 50
    )
    assert qd.achar_passagens(resultado, AGORA) == []


def test_link_oficial_so_de_site_oficial_com_caminho():
    janela = "Inscrições em https://forms.gle/abc123 e instagram https://instagram.com/x. Edital completo: https://www.guaracai.sp.gov.br/editais/pnab-2026.pdf, ok. Home https://guaracai.sp.gov.br/"
    assert qd.link_oficial(janela) == "https://www.guaracai.sp.gov.br/editais/pnab-2026.pdf"
    assert qd.link_oficial("só https://exemplo.com.br/edital e https://guaracai.sp.gov.br") is None


# ── API simulada ─────────────────────────────────────────────────────────────────────────────


def gazeta(nome):
    g = dict(ORIGEM[nome]["gazette"])
    return {**g, "excerpts": ORIGEM[nome]["excerpts"]}


def api(diarios, textos, falhar=None, pedidos=None):
    """`diarios`: gazetas devolvidas por QUALQUER consulta; `falhar`: dict host -> (status|Exception)."""

    def tratar(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if req.url.path.endswith("/robots.txt"):
            return httpx.Response(404)
        if pedidos is not None:
            pedidos.append(url)
        f = (falhar or {}).get(req.url.host)
        if isinstance(f, Exception):
            raise f
        if isinstance(f, int):
            return httpx.Response(f, text="<html>erro</html>")
        if req.url.path.endswith("/gazettes"):
            off = int(req.url.params.get("offset", "0"))
            lote = diarios[off : off + int(req.url.params.get("size", "50"))]
            return httpx.Response(200, json={"total_gazettes": len(diarios), "gazettes": lote})
        txt = textos.get(url)
        return (
            httpx.Response(404)
            if txt is None
            else httpx.Response(200, text=txt, headers={"content-type": "text/plain"})
        )

    async def sem_espera(_):
        return None

    return ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera)


def fonte(**kw):
    return FonteConfig(
        **{
            **dict(
                id="querido-diario",
                nome="QD",
                tipo="descoberta",
                metodo="api",
                coletor="querido_diario",
                url="https://api.queridodiario.org.br",
            ),
            **kw,
        }
    )


def textos_reais():
    return {
        ORIGEM[n]["gazette"]["txt_url"]: ler(n)
        for n in ("guaracai", "rondonopolis", "salvador_inventario")
    }


def coletar(diarios, textos=None, falhar=None, pedidos=None, **kw):
    async def corrida():
        async with api(
            diarios, textos if textos is not None else textos_reais(), falhar, pedidos
        ) as c:
            return await qd.coletar(c, fonte(), 0, AGORA, **kw)

    return asyncio.run(corrida())


def todos_os_diarios():
    return [gazeta(n) for n in ("guaracai", "rondonopolis", "salvador_inventario")]


def test_coleta_real_acha_os_dois_editais_e_descarta_o_que_nao_e_pedido_de_inscricao():
    r = coletar(todos_os_diarios())
    assert r.status == "ok" and not r.brutos, "o Querido Diário só descobre: nunca publica direto"
    por_mun = {c.campos["municipio"]: c for c in r.candidatos}
    assert set(por_mun) == {"Guaraçaí", "Rondonópolis"}
    g = por_mun["Guaraçaí"]
    assert (g.campos["uf"], g.campos["abrangencia"], g.campos["orgao_fomento"]) == (
        "SP",
        "municipal",
        "Município de Guaraçaí",
    )
    assert (
        g.campos["inscricao_fim"] == "2026-11-12"
        and "inscri" in g.campos["evidencias"]["inscricao_fim"].lower()
    )
    assert (
        g.origem.startswith("Diário Oficial de Guaraçaí (SP), de 2026-11-06")
        and "Querido Diário" in g.origem
    )
    assert g.link_oficial == ORIGEM["guaracai"]["gazette"]["url"], (
        "sem link oficial no texto: o espelho do diário, para conferência humana"
    )
    assert "cópia do diário" in g.motivo and g.chave.startswith("qd:3517802:2026-11-06:")
    assert 0 < len(g.resumo) <= 601
    assert r.estado["total_links"] == 3 and len(r.estado["gazetas"]) == 2, (
        "o de Salvador (trecho sem sinal) nem é baixado"
    )


def test_candidato_do_diario_vira_issue_com_uf_e_municipio_no_yaml():
    c = coletar(todos_os_diarios()).candidatos[0]
    y = yaml.safe_load(c.yaml_proposto())
    assert (
        y["uf"] in ("SP", "MT")
        and y["municipio"]
        and y["abrangencia"] == "municipal"
        and y["inscricao_fim"]
    )
    assert y["evidencias"]["inscricao_fim"] and marcador(c.chave) in c.corpo()


def test_link_oficial_citado_no_diario_vira_o_link_do_candidato(monkeypatch):
    original = qd.link_oficial
    monkeypatch.setattr(
        qd,
        "link_oficial",
        lambda j: (
            "https://www.guaracai.sp.gov.br/editais/pnab-2026.pdf"
            if "02/2026" in j
            else original(j)
        ),
    )
    c = next(
        x for x in coletar(todos_os_diarios()).candidatos if x.campos["municipio"] == "Guaraçaí"
    )
    assert (
        c.link_oficial == "https://www.guaracai.sp.gov.br/editais/pnab-2026.pdf"
        and "cita o link acima" in c.motivo
    )


def test_ja_processado_e_ja_na_fila_e_ja_no_portal_nao_viram_candidato_de_novo():
    primeiro = coletar(todos_os_diarios())
    estado = primeiro.estado
    pedidos: list[str] = []
    r2 = coletar(todos_os_diarios(), estado=estado, pedidos=pedidos)
    assert (
        r2.candidatos == []
        and r2.descartes["ja_processada"] == 2
        and not [p for p in pedidos if p.endswith(".txt")]
    ), "nem baixa o diário de novo"
    na_fila = {marcador(c.chave) for c in primeiro.candidatos}
    r3 = coletar(todos_os_diarios(), marcadores=na_fila)
    assert r3.candidatos == [] and r3.descartes["ja_na_fila"] == 2
    existente = Edital(fonte="mapas:sp", id_externo="1", url_origem="https://x.sp.gov.br/a", titulo="Edital de Chamamento Público 02/2026 Guaraçaí fomento", orgao_fomento="x",
                       uf="SP", inscricao_fim=datetime(2026, 11, 13, 2, 59, 59, tzinfo=UTC))  # fmt: skip
    r4 = coletar(todos_os_diarios(), existentes=[existente])
    assert [c.campos["municipio"] for c in r4.candidatos] == ["Rondonópolis"] and r4.descartes[
        "ja_no_portal"
    ] == 1
    r5 = coletar(
        todos_os_diarios(), urls_conhecidas={qd.normalizar_url(primeiro.candidatos[0].link_oficial)}
    )  # espelho conhecido não vale como "já no portal"
    assert len(r5.candidatos) >= 1


def test_limites_de_candidatos_e_de_diarios_lidos(monkeypatch):
    monkeypatch.setattr(qd, "MAX_CANDIDATOS", 1)
    r = coletar(todos_os_diarios())
    assert len(r.candidatos) == 1 and r.descartes["limite_de_candidatos"] == 1
    assert r.candidatos[0].campos["inscricao_fim"] == "2026-11-12", "ficam os de prazo mais próximo"
    monkeypatch.setattr(qd, "MAX_GAZETAS", 1)
    r2 = coletar(todos_os_diarios())
    assert r2.descartes["limite_de_diarios"] == 1 and len(r2.estado["gazetas"]) == 1


def test_diario_sem_sinal_nenhum_nem_e_baixado():
    ruido = {
        "territory_id": "1",
        "territory_name": "X",
        "state_code": "SP",
        "date": "2026-11-06",
        "txt_url": "https://data.queridodiario.ok.org.br/x.txt",
        "url": "https://data/x",
        "excerpts": ["FONTE 1501 SALDO 1.445,13"],
    }
    pedidos: list[str] = []
    r = coletar([ruido], textos={}, pedidos=pedidos)
    assert (
        r.candidatos == []
        and r.descartes["trecho_sem_sinal_de_edital"] == 1
        and not [p for p in pedidos if p.endswith(".txt")]
    )


# ── API instável ─────────────────────────────────────────────────────────────────────────────


def test_erro_passageiro_da_api_tem_nova_tentativa_e_erro_de_ssl_troca_de_endereco():
    pedidos: list[str] = []
    r = coletar(
        todos_os_diarios(),
        falhar={
            "api.queridodiario.org.br": httpx.ConnectError("[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE]")
        },
        pedidos=pedidos,
    )
    assert len(r.candidatos) == 2 and r.descartes["erro_consulta"] == 0
    assert len([p for p in pedidos if "api.queridodiario.org.br" in p]) <= 2, (
        "o endereço com SSL quebrado é abandonado, sem insistir seis vezes"
    )
    assert any("queridodiario.ok.org.br/api/gazettes" in p for p in pedidos)


def test_api_fora_do_ar_levanta_erro_em_vez_de_dizer_que_nao_ha_editais():
    with pytest.raises(qd.ErroApi, match="fora do ar"):
        coletar(
            todos_os_diarios(),
            falhar={
                "api.queridodiario.org.br": 520,
                "queridodiario.ok.org.br": 520,
                "api.queridodiario.ok.org.br": 520,
            },
        )


def test_diario_que_nao_baixa_nao_derruba_os_outros():
    textos = textos_reais()
    del textos[ORIGEM["guaracai"]["gazette"]["txt_url"]]
    r = coletar(todos_os_diarios(), textos=textos)
    assert [c.campos["municipio"] for c in r.candidatos] == ["Rondonópolis"] and r.descartes[
        "erro_diario:ErroWeb"
    ] == 1


def test_paginacao_pelo_offset_junta_as_paginas(monkeypatch):
    monkeypatch.setattr(qd, "POR_PAGINA", 1)
    pedidos: list[str] = []
    r = coletar(todos_os_diarios(), pedidos=pedidos)
    assert r.estado["total_links"] == 3 and any("offset=2" in p for p in pedidos)


# ── configuração ─────────────────────────────────────────────────────────────────────────────


def test_fonte_do_catalogo_e_so_descoberta_sem_llm_e_com_termos_conferidos():
    f = next(x for x in carregar_fontes_v4(FONTES) if x.id == "querido-diario")
    assert (
        f.tipo == "descoberta"
        and f.coletor == "querido_diario"
        and f.ativa
        and f.termos_conferidos_em == "2026-11-08"
    )
    assert f.intervalo_min_entre_requisicoes_s >= 2 and f.robots_ok is True
    texto = (RAIZ / "worker" / "fontes.yaml").read_text(encoding="utf-8")
    assert "ai-train=no" in texto, (
        "a decisão de não usar LLM nos textos do serviço fica documentada ao lado da fonte"
    )
    codigo = (RAIZ / "worker" / "coletores" / "querido_diario.py").read_text(encoding="utf-8")
    assert (
        "enriquecimento_llm" not in codigo and "ClienteGemini" not in codigo and "import" in codigo
    )


def test_despacho_e_existentes_chegam_ao_coletor():
    v4 = (RAIZ / "worker" / "fontes_v4.py").read_text(encoding="utf-8")
    sw = (RAIZ / "worker" / "scraper_worker.py").read_text(encoding="utf-8")
    assert 'f.coletor == "querido_diario"' in v4 and "existentes" in v4 and "existentes=[" in sw
    assert Candidato(chave="x", titulo="t", link_oficial="https://x", origem="o").yaml_proposto()
