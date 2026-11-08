"""Publicação automática, modo sombra: regras de decisão (worker/automatica.py), com textos reais de editais."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml

from worker import automatica as a
from worker.web import ClienteWeb

RAIZ = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures" / "automatica"
ORIGEM = json.loads((FIX / "ORIGEM.json").read_text(encoding="utf-8"))["itens"]
HOJE = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
DOMINIOS = a.carregar_dominios()


def real(nome, **kw):
    meta = ORIGEM[nome]
    texto = (FIX / f"{nome}.txt").read_text(encoding="utf-8")
    return a.avaliar(
        meta["numero"],
        meta["titulo_issue"],
        kw.pop("link", meta["link"]),
        kw.pop("texto", texto),
        kw.pop("hoje", HOJE),
        DOMINIOS,
        **kw,
    )


# ── editais reais: o que publica e com que dados ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "nome, orgao, uf, abrangencia, prazo",
    [
        ("niteroi", "Prefeitura Municipal de Niterói", "RJ", "municipal", "2026-11-09"),
        ("tanabi", "Município de Tanabi", "SP", "municipal", "2026-11-18"),
        ("gurupi", "Prefeitura Municipal de Gurupi", "TO", "municipal", "2026-11-11"),
        ("riofilme12", "Riofilme", "RJ", "municipal", "2026-12-02"),
        ("londrina", "Município de Londrina", "PR", "municipal", "2026-11-10"),
    ],
)
def test_edital_real_de_dominio_oficial_com_prazo_e_orgao_e_publicavel(
    nome, orgao, uf, abrangencia, prazo
):
    d = real(nome)
    assert d.publica, d.motivos
    assert (
        d.campos["orgao_fomento"],
        d.campos["uf"],
        d.campos["abrangencia"],
        d.campos["inscricao_fim"],
    ) == (orgao, uf, abrangencia, prazo)
    assert (
        d.campos["linguagens"] == ["Multiáreas"]
        and d.campos["titulo"]
        and len(d.campos["resumo_simples"]) <= 280
    )


def test_toda_evidencia_do_resultado_existe_literalmente_no_texto():
    for nome in ("niteroi", "tanabi", "gurupi", "riofilme12", "londrina"):
        d = real(nome)
        texto = (FIX / f"{nome}.txt").read_text(encoding="utf-8")
        assert d.evidencias["inscricao_fim"] and a.trecho_no_texto(
            d.evidencias["inscricao_fim"], texto
        ), nome
        if not d.evidencias["orgao_fomento"].startswith("domínio aprovado"):
            assert a.trecho_no_texto(d.evidencias["orgao_fomento"], texto), nome
        assert "inscri" in a.sem_acento(d.evidencias["inscricao_fim"])


def test_cabecalho_governo_federal_nao_vira_o_orgao_de_uma_prefeitura():
    """No edital de Tanabi o PNAB abre com 'GOVERNO FEDERAL': o órgão é o município do domínio."""
    d = real("tanabi")
    assert d.campos["orgao_fomento"] == "Município de Tanabi"
    assert a.conferir_municipio("Governo Federal", "tanabi.sp.gov.br") is None


def test_lixo_de_menu_depois_do_nome_da_cidade_e_cortado():
    assert (
        a.conferir_municipio(
            "Município de Piracicaba Cidadão Empresa Turista", "piracicaba.sp.gov.br"
        )
        == "Município de Piracicaba"
    )
    assert (
        a.conferir_municipio("Prefeitura Municipal de Niterói", "smc.niteroi.rj.gov.br")
        == "Prefeitura Municipal de Niterói"
    )
    assert (
        a.conferir_municipio("Município de São Carlos", "cidadao.saocarlos.sp.gov.br")
        == "Município de São Carlos"
    )
    assert a.conferir_municipio("Município de Salvador", "tanabi.sp.gov.br") is None, (
        "o nome tem de bater com o domínio"
    )


def test_edital_de_outra_cidade_no_dominio_errado_e_barrado():
    d = real("tanabi", link="https://pelotas.rs.gov.br/edital.pdf")
    assert not d.publica and "não confere com o domínio" in d.motivos[0]


def test_piracicaba_com_inscricoes_encerradas_no_titulo_fica_na_fila_humana():
    d = real("piracicaba")
    assert not d.publica and "encerramento" in d.motivos[0]


# ── cada regra, uma a uma ────────────────────────────────────────────────────────────────────


GENERICO = (
    "PREFEITURA MUNICIPAL DE EXEMPLO EDITAL DE CHAMAMENTO PÚBLICO Nº 1/2026 A Prefeitura torna público o edital de apoio financeiro "
    "a projetos culturais, no valor de R$ 100.000,00. As inscrições serão gratuitas e deverão ser realizadas de 01/11/2026 a 20/11/2026. "
    + "Demais regras do edital seguem nos itens abaixo. "
    * 8
)


def barrado(motivo, **kw):
    kw.setdefault("link", "https://exemplo.sp.gov.br/edital.pdf")
    kw.setdefault("texto", GENERICO)
    d = a.avaliar(
        1,
        "Edital de Chamamento Público 1/2026",
        kw.pop("link"),
        kw.pop("texto"),
        kw.pop("hoje", HOJE),
        DOMINIOS,
        **kw,
    )
    assert not d.publica and motivo in d.motivos[0], d.motivos
    return d


def test_veto_humano():
    barrado("veto humano", rotulos={"candidato", "nao-publicar"})


def test_dominio_nao_oficial_e_barrado_e_o_aprovado_na_lista_passa():
    barrado("domínio não oficial", link="https://diariomunicipal.com.br/niteroi/edital.pdf")
    barrado("domínio não oficial", link="https://mapacultural.exemplo.com.br/x")
    assert (
        a.eh_oficial("riofilme.com.br", DOMINIOS) == "lista"
        and a.eh_oficial("cdn.riofilme.com.br", DOMINIOS) == "lista"
    )
    assert (
        a.eh_oficial("gov.br", {}) == "sufixo"
        and a.eh_oficial("x.sp.gov.br", {}) == "sufixo"
        and a.eh_oficial("camara.leg.br", {}) == "sufixo"
    )
    assert (
        a.eh_oficial("gov.br.malicioso.com", {}) is None and a.eh_oficial("notgov.br", {}) is None
    )


def test_pagina_sem_texto_legivel():
    barrado("sem texto legível", texto="")
    barrado("sem texto legível", texto="Carregando...")


def test_prazo_ausente_vencido_ou_distante():
    barrado(
        "prazo de inscrição não encontrado",
        texto=GENERICO.replace("de 01/11/2026 a 20/11/2026", "em breve"),
    )
    barrado("prazo já passou", hoje=datetime(2026, 11, 25, tzinfo=UTC))
    barrado("prazo muito distante", hoje=datetime(2025, 1, 1, tzinfo=UTC))


def test_data_sem_ano_ou_fora_de_inscricao_nao_basta_para_o_prazo():
    texto = (
        "EDITAL DE CHAMAMENTO PÚBLICO Nº 1/2026 PREFEITURA MUNICIPAL DE NITERÓI apoio financeiro R$ 100.000,00. "
        + "Texto do edital. " * 30
        + " Inscrições até 30/12."
    )
    barrado("prazo de inscrição não encontrado", texto=texto)


@pytest.mark.parametrize(
    "titulo",
    [
        "Resultado final do Edital 01/2026",
        "Errata ao edital 03/2026",
        "Inscrições para expositores da feira literária",
        "Edital de curso de teatro",
        "Homologação das inscrições do edital 5/2026",
        "Edital 02/2026 (suspenso)",
    ],
)
def test_titulos_de_ruido_ou_ato_posterior(titulo):
    d = a.avaliar(
        1,
        titulo,
        ORIGEM["niteroi"]["link"],
        (FIX / "niteroi.txt").read_text(encoding="utf-8"),
        HOJE,
        DOMINIOS,
    )
    assert not d.publica and "título de ruído" in d.motivos[0]


def test_texto_que_nao_e_de_fomento_e_barrado():
    texto = (
        "A Prefeitura Municipal de Niterói informa o horário de funcionamento da biblioteca. " * 10
        + " inscrições até 12/11/2026."
    )
    barrado("não parece edital", texto=texto)


def test_orgao_nao_encontrado_ou_sem_abrangencia_reconhecida():
    d = a.avaliar(
        1,
        "Edital de Chamamento Público 1/2026",
        "https://x.sp.gov.br/e",
        GENERICO.replace("PREFEITURA MUNICIPAL DE EXEMPLO", "O ÓRGÃO"),
        HOJE,
        DOMINIOS,
    )
    assert not d.publica and "órgão não encontrado" in d.motivos[0]
    # Fundação/Instituto fora de domínio aprovado: não publica sozinho
    assert a.abrangencia_do_orgao("Fundação Rio das Ostras") is None
    assert (
        a.abrangencia_do_orgao("Governo do Estado de Goiás") == "estadual"
        and a.abrangencia_do_orgao("Município de X") == "municipal"
    )


def test_uf_so_pelo_dominio_ou_lista():
    assert (
        a.uf_do_host("secult.ce.gov.br") == "CE"
        and a.uf_do_host("x.leg.br") is None
        and a.uf_do_host("riofilme.com.br") is None
    )
    assert a.uf_do_host("camara.zz.gov.br") is None, "sigla que não é UF não vale"


def test_teto_por_execucao_fica_com_os_de_prazo_mais_proximo():
    ds = []
    for i in range(20):
        d = a.Decisao(i, f"e{i}", "https://x.sp.gov.br", publica=True)
        d.campos = {"inscricao_fim": (HOJE + timedelta(days=20 - i)).date().isoformat()}
        ds.append(d)
    a.aplicar_teto(ds)
    assert sum(d.publica for d in ds) == 15
    assert all(not d.publica and "teto" in d.motivos[0] for d in ds[:5]), (
        "os de prazo mais distante é que ficam de fora"
    )


def test_titulo_do_issue_ruim_e_substituido_pelo_do_documento():
    texto = (FIX / "riofilme12.txt").read_text(encoding="utf-8")
    assert a.melhor_titulo("DISTRIBUIDORA DE FILMES S/A - RIOFILME", texto).startswith(
        "EDITAL RIOFILME/FSA Nº 12"
    )
    assert a.melhor_titulo(
        "PREFEITURA", (FIX / "niteroi.txt").read_text(encoding="utf-8")
    ).startswith("EDITAL DE CHAMAMENTO PÚBLICO SMC Nº 01/2026")
    assert (
        a.melhor_titulo("Edital de Chamamento 3/2026 Errata 1 Errata 2 Anexo 1 – Categorias", "")
        == "Edital de Chamamento 3/2026"
    )
    assert a.melhor_titulo("EDITAL", "Texto sem número de edital nenhum.") is None
    assert a.melhor_titulo("Edital Ateliê abre inscrições; confira", "") is None


# ── leitura das Issues, relatório e CLI ──────────────────────────────────────────────────────


def issue(n, titulo, link, rotulos=("candidato",), extra=""):
    return {"number": n, "title": f"Candidato: {titulo}", "labels": [{"name": r} for r in rotulos],
            "body": f"<!-- candidato:abc -->\n**Origem:** x\n**Link oficial:** {link}\n{extra}"}  # fmt: skip


def web_falso(paginas):
    pedidos = []

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/robots.txt"):
            return httpx.Response(404)
        pedidos.append(str(req.url))
        r = paginas.get(str(req.url), 404)
        return (
            httpx.Response(r, text="x")
            if isinstance(r, int)
            else httpx.Response(200, text=r, headers={"content-type": "text/html"})
        )

    async def sem_espera(_):
        return None

    return ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera), pedidos


def test_avaliar_candidatos_nao_le_pagina_de_dominio_barrado_e_trata_falha_de_leitura():
    html = (
        "<html><body><main><p>"
        + (FIX / "niteroi.txt").read_text(encoding="utf-8")
        + "</p></main></body></html>"
    )
    bom = "https://smc.niteroi.rj.gov.br/edital-pnab/"  # página HTML (o link real é um PDF)
    issues = [
        issue(1, "Edital de Niterói", bom),
        issue(2, "Edital fora", "https://blog.exemplo.com/edital"),
        issue(3, "Edital vetado", "https://x.sp.gov.br/e", rotulos=("candidato", "nao-publicar")),
        issue(4, "Edital com site fora", "https://fora.sp.gov.br/e"),
        {"number": 5, "title": "Candidato: sem link", "labels": [], "body": "nada"},
    ]
    cliente, pedidos = web_falso({bom: html})

    async def rodar():
        async with cliente:
            return await a.avaliar_candidatos(issues, cliente, DOMINIOS, HOJE)

    ds = {d.numero: d for d in asyncio.run(rodar())}
    assert ds[1].publica and ds[1].campos["uf"] == "RJ"
    assert "domínio não oficial" in ds[2].motivos[0] and "veto humano" in ds[3].motivos[0]
    assert "não foi possível ler a página" in ds[4].motivos[0] and ds[5].motivos == [
        "Issue sem link oficial"
    ]
    assert sorted(p.split("/")[2] for p in pedidos) == [
        "fora.sp.gov.br",
        "smc.niteroi.rj.gov.br",
    ], "domínio barrado e vetado não geram requisição"


def test_orgao_estruturado_do_cultbr_vem_do_yaml_da_issue():
    corpo = "```yaml\norgao_fomento: Município de Pelotas\ntitulo: x\n```"
    assert a.orgao_da_issue(corpo) == (
        "Município de Pelotas",
        "CultBR: nome_ente_recebedor = Município de Pelotas",
    )
    assert a.orgao_da_issue("sem yaml") is None and a.orgao_da_issue("```yaml\nx: [\n```") is None


def test_relatorio_lista_o_que_publicaria_e_o_porque_do_resto():
    ds = [real("niteroi"), real("tanabi", link="https://blog.exemplo.com/x")]
    md = a.relatorio_md(ds, HOJE)
    assert (
        "**1 de 2**" in md
        and "Nada foi publicado" in md
        and "Prefeitura Municipal de Niterói" in md
    )
    assert "domínio não oficial (blog.exemplo.com)" in md and "| Motivo | Qtd |" in md


def test_cli_sem_token_nao_faz_nada(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert asyncio.run(a.principal([])) == 0
    assert "nada a fazer" in capsys.readouterr().out


def test_lista_de_dominios_aprovados_tem_a_forma_certa():
    d = json.loads(
        (RAIZ / "worker" / "referencia" / "dominios_oficiais.json").read_text(encoding="utf-8")
    )
    hosts = [x["host"] for x in d["dominios"]]
    assert len(hosts) == len(set(hosts)) and {"riofilme.com.br", "spcine.com.br"} <= set(hosts)
    for x in d["dominios"]:
        assert x["abrangencia"] in ("nacional", "estadual", "municipal") and x["orgao"]
        assert x["abrangencia"] == "nacional" or x.get("uf"), (
            f"{x['host']}: estadual/municipal precisa de UF"
        )
        assert not x["host"].endswith((".gov.br", ".leg.br")), (
            "terminação oficial já vale sem lista"
        )


def test_workflow_roda_o_modo_sombra_em_job_proprio_sem_publicar():
    texto = (RAIZ / ".github" / "workflows" / "sync_editais.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(texto)
    j = wf["jobs"]["publicacao_sombra"]
    assert j["needs"] == "sync" and j["continue-on-error"] is True and j["timeout-minutes"] <= 20
    assert "python -m worker.automatica" in texto
    passo = next(p for p in j["steps"] if "automatica" in p.get("run", ""))
    assert "GITHUB_TOKEN" in passo["env"] and "SUPABASE" not in json.dumps(passo["env"]), (
        "o modo sombra não toca no banco"
    )


def test_prazo_e_a_data_de_brasilia_em_qualquer_fuso_da_maquina(monkeypatch):
    """Regressão: `astimezone()` sem fuso usava o da máquina; no Actions (UTC) o prazo saía um dia adiantado."""
    import time

    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        d = real("gurupi")
        assert d.campos["inscricao_fim"] == "2026-11-12" or d.publica
        texto = "Prazo de inscrição: as inscrições vão até 11 de novembro de 2026."
        from worker.descoberta import campos_do_texto

        assert campos_do_texto(texto)["inscricao_fim"] == "2026-11-11", (
            "23h59 de Brasília é 02h59 UTC do dia seguinte"
        )
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
