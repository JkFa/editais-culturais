"""Coletor `govbr` (Funarte, Iphan, Palmares, Biblioteca Nacional): páginas reais de 08/11/2026 salvas como fixtures."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from worker.catalogo import FonteConfig, carregar_fontes_v4
from worker.coletores import govbr
from worker.coletores.minc import parse_listagem
from worker.scraper_worker import FONTES
from worker.web import ClienteWeb

FIX = Path(__file__).parent / "fixtures" / "govbr"
ORIGEM = json.loads((FIX / "ORIGEM.json").read_text(encoding="utf-8"))
AGORA = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
LISTA_FUNARTE = "https://www.gov.br/funarte/pt-br/editais/editais-abertos"
LISTA_IPHAN = "https://www.gov.br/iphan/pt-br/acesso-a-informacao/editais-e-selecoes/abertos"


def ler(nome):
    return (FIX / nome).read_text(encoding="utf-8")


def web(rotas, pedidos=None):
    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/robots.txt"):
            return httpx.Response(404)
        if pedidos is not None:
            pedidos.append(str(req.url))
        r = rotas.get(str(req.url))
        if r is None:
            return httpx.Response(404)
        if isinstance(r, int):
            return httpx.Response(r)
        if isinstance(r, bytes):
            return httpx.Response(200, content=r, headers={"content-type": "application/pdf"})
        return httpx.Response(200, text=r, headers={"content-type": "text/html"})

    async def sem_espera(_):
        return None

    return ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera)


def coletar(fonte, rotas, agora=AGORA, pedidos=None):
    async def corrida():
        async with web(rotas, pedidos) as c:
            return await govbr.coletar(c, fonte, 0, agora)

    return asyncio.run(corrida())


def fonte(**kw):
    base = dict(
        id="x",
        nome="X",
        orgao="Órgão X",
        tipo="oficial",
        metodo="html",
        coletor="govbr",
        url=LISTA_FUNARTE,
    )
    return FonteConfig(**{**base, **kw})


# ── Funarte (cards) ──────────────────────────────────────────────────────────────────────────


def rotas_funarte():
    itens = parse_listagem(ler("funarte_abertos.html"))
    rotas = {LISTA_FUNARTE: ler("funarte_abertos.html")}
    for u, _ in itens:
        if "bolsa" in u:
            rotas[u] = ler("funarte_bolsa.html")
        if "programa-funarte-aberta" in u:
            rotas[u] = ler("funarte_programa_aberta.html")
    return rotas


def test_funarte_publica_so_o_que_tem_prazo_futuro_com_trecho_e_manda_o_resto_para_a_fila():
    f = fonte(id="funarte-editais", orgao="Funarte", formato="cards")
    r = coletar(f, rotas_funarte())
    assert r.status == "ok" and len(r.brutos) == 1
    b = r.brutos[0]
    assert (
        b.titulo.startswith("IV Bolsa Funarte")
        and b.orgao_fomento == "Funarte"
        and b.abrangencia == "nacional"
    )
    assert b.inscricao_fim == datetime(2026, 12, 19, 2, 59, 59, tzinfo=UTC), (
        "18/12 23h59 em Brasília"
    )
    assert (
        b.proveniencia["inscricao_fim"].via == "regex"
        and "18 DE DEZEMBRO DE 2026" in b.proveniencia["inscricao_fim"].trecho
    )
    assert b.url_edital_pdf and b.url_edital_pdf.endswith(".pdf")
    assert [c.titulo for c in r.candidatos] == ["Programa Funarte Aberta"], (
        "fluxo contínuo, sem prazo: conferência humana"
    )
    assert r.descartes["sem_prazo"] == 1 and r.descartes["erro:ErroWeb"] == 2, (
        "as outras duas páginas não existem no teste"
    )
    assert r.candidatos[0].campos["orgao_fomento"] == "Funarte"


def test_edital_com_prazo_vencido_e_descartado():
    r = coletar(fonte(formato="cards"), rotas_funarte(), agora=datetime(2027, 1, 5, tzinfo=UTC))
    assert r.brutos == [] and r.descartes["encerrado"] == 1


def test_pagina_que_responde_403_nao_e_contornada_e_nao_derruba_as_outras():
    rotas = rotas_funarte()
    bolsa = next(u for u in rotas if "bolsa" in u)
    rotas[bolsa] = 403
    pedidos: list[str] = []
    r = coletar(fonte(formato="cards"), rotas, pedidos=pedidos)
    assert r.brutos == [] and r.descartes["erro:BloqueioDeAcesso"] == 1
    assert pedidos.count(bolsa) == 1, "403 é uma tentativa só: nada de insistir nem disfarçar"
    assert [c.titulo for c in r.candidatos] == ["Programa Funarte Aberta"]


def test_layout_mudou_nenhum_item_vira_sem_dados_sem_erro():
    r = coletar(fonte(formato="cards"), {LISTA_FUNARTE: "<html><body>nada aqui</body></html>"})
    assert r.status == "sem_dados" and "layout" in r.detalhe and not r.brutos


def test_varias_listas_da_mesma_fonte_sao_lidas_e_repetidos_somem():
    rotas = rotas_funarte()
    outra = "https://www.gov.br/funarte/pt-br/editais/em-andamento"
    rotas[outra] = ler("funarte_abertos.html")  # mesma lista: nada pode ser coletado duas vezes
    pedidos: list[str] = []
    r = coletar(fonte(formato="cards", paginas=[LISTA_FUNARTE, outra]), rotas, pedidos=pedidos)
    assert len(r.brutos) == 1 and outra in pedidos
    assert len([p for p in pedidos if "bolsa" in p]) == 1


# ── filtro do que não é fomento ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "titulo, esperado",
    [
        ("IV Bolsa Funarte de Estímulo à Conservação Fotográfica", True),
        ("Prêmio Mário de Andrade de Fotografias Etnográficas - Edição 2026", True),
        ("Edital de Termo de Execução Cultural nº 1/2025 - Iphan-DF", True),
        ("Edital de Chamamento Público nº 02/2026 - Adote um projeto", True),
        ("Projeto 914BRZ4027 Edital nº 64/2026: Perfil Arquitetura ou Engenharia", False),
        ("Edital de seleção de consultor individual", False),
        ("Processo seletivo para estágio", False),
        ("Edital de mestrado profissional em preservação", False),
        ("Resultado final do Edital 11/2025", False),
        ("Retificação do edital", False),
        ("Parecer de Reavaliação do Fandango Caiçara", False),
        ("Acordo de Cooperação Técnica nº 1/2025", False),
        ("Programa Ibermúsicas - Convocatórias 2026", False),
        ("Programa Ibercena - Convocatória 2026-2027", False),
        ("Edital de iniciação científica PAP-IC", False),
        ("Extrato de Justificativa para Ausência de Chamamento Público", False),
        ("Eleição de representantes do conselho", False),
    ],
)
def test_so_fomento_entra(titulo, esperado):
    assert govbr.e_fomento(titulo, "https://www.gov.br/x/pt-br/editais/abertos/pagina") is esperado


def test_endereco_com_sei_ou_perfil_tambem_barra_e_incluir_titulo_restringe():
    assert not govbr.e_fomento(
        "Edital", "https://www.gov.br/x/editais/sei_7880794_edital_iphan_n__11.pdf"
    )
    assert govbr.e_fomento("Prêmio Camões", "https://x/y", incluir=["pr[eê]mio", "tradu"])
    assert not govbr.e_fomento(
        "Apresentação do Centro de Cooperação", "https://x/y", incluir=["pr[eê]mio", "tradu"]
    )
    assert not govbr.e_fomento("Edital de Teatro", "https://x/y", extras=["teatro"])


# ── Iphan (estado React embutido + PDF do edital) ────────────────────────────────────────────


def test_volto_le_os_itens_do_estado_sem_arquivos_menu_nem_duplicata():
    itens = govbr.parse_volto(ler("iphan_abertos.html"), LISTA_IPHAN)
    titulos = [t for _, t in itens]
    assert len(itens) == 17 and len(set(t.lower() for t in titulos)) == 17, (
        "os '-1' duplicados e os PDFs soltos ficam de fora"
    )
    assert any(t.startswith("CONCURSO SÍLVIO ROMERO") for t in titulos) and any(
        "Prêmio Mário de Andrade" in t for t in titulos
    )
    assert all(
        u.startswith(LISTA_IPHAN + "/") and "@" not in u and not u.endswith(".pdf")
        for u, _ in itens
    )
    assert "Edital nº 1/2026 - Seleção de expositores" in " ".join(titulos), (
        "acentos do estado são lidos certo"
    )


def test_pdfs_do_estado_aponta_o_arquivo_filho_da_pagina():
    u = ORIGEM["iphan_concurso"]["url"]
    pdfs = govbr.pdfs_do_estado(ler("iphan_concurso.html"), u)
    assert (
        len(pdfs) == 1
        and pdfs[0][0] == ORIGEM["iphan_concurso"]["pdf"]
        and pdfs[0][0].endswith("/@@display-file/file")
    )


def test_iphan_le_o_prazo_no_pdf_quando_a_pagina_nao_traz():
    """A página do concurso quase não tem texto: o prazo está no PDF anexado (texto lido de verdade, com pypdf)."""
    f = fonte(id="iphan-editais", orgao="Iphan", formato="volto", url=LISTA_IPHAN, max_itens=40)
    rotas = {
        LISTA_IPHAN: ler("iphan_abertos.html"),
        ORIGEM["iphan_concurso"]["url"]: ler("iphan_concurso.html"),
        ORIGEM["iphan_concurso"]["pdf"]: (FIX / "iphan_concurso_edital.pdf").read_bytes(),
    }
    r = coletar(f, rotas, agora=datetime(2026, 7, 10, 12, tzinfo=UTC))
    assert len(r.brutos) == 1
    b = r.brutos[0]
    assert b.titulo.startswith("CONCURSO SÍLVIO ROMERO") and b.orgao_fomento == "Iphan"
    assert (
        b.inscricao_fim == datetime(2026, 8, 5, 2, 59, 59, tzinfo=UTC)
        and "04 de a" in b.proveniencia["inscricao_fim"].trecho
    )
    assert b.url_edital_pdf == ORIGEM["iphan_concurso"]["pdf"].removesuffix("/@@display-file/file")
    assert r.descartes["nao_e_fomento"] == 9
    depois = coletar(f, rotas, agora=AGORA)
    assert depois.brutos == [] and depois.descartes["encerrado"] == 1, (
        "em 08/11 o concurso já encerrou"
    )


# ── Palmares (links) e Biblioteca Nacional (cards filtrados) ─────────────────────────────────


def test_palmares_so_pega_links_de_edital_do_miolo_e_sem_prazo_vai_para_a_fila():
    lista = "https://www.gov.br/palmares/pt-br/acesso-a-informacao/editais"
    links = govbr.parse_links(
        ler("palmares_editais.html"), lista, r"edital|chamad|sele[cç]|pr[eê]mio|ocupa"
    )
    assert len(links) == 1 and links[0][1].startswith("Edital nº 02/2026"), (
        "o PDF 'Como inscrever' e o menu não entram"
    )
    f = fonte(
        id="palmares-editais",
        orgao="Fundação Cultural Palmares",
        formato="links",
        url=lista,
        padrao_links="edital|chamad|sele[cç]|pr[eê]mio|ocupa",
    )
    r = coletar(
        f, {lista: ler("palmares_editais.html"), links[0][0]: ler("palmares_ocupacao.html")}
    )
    assert (
        r.brutos == []
        and len(r.candidatos) == 1
        and r.candidatos[0].campos["orgao_fomento"] == "Fundação Cultural Palmares"
    )
    assert r.candidatos[0].chave == f"palmares-editais:{links[0][0]}"


def test_biblioteca_nacional_ignora_paginas_institucionais_da_lista_de_programas():
    lista = "https://www.gov.br/bn/pt-br/atuacao/cooperacao-e-difusao"
    todos = [t for _, t in parse_listagem(ler("bn_cooperacao.html"))]
    assert len(todos) == 7
    incluir = [
        "pr[eê]mio",
        "tradu",
        "edital",
        "chamada",
        "bolsa",
        "sele[cç][aã]o",
        "concurso",
        "apoio",
    ]
    f = fonte(id="bn-editais", formato="cards", url=lista, incluir_titulo=incluir)
    pedidos: list[str] = []
    r = coletar(f, {lista: ler("bn_cooperacao.html")}, pedidos=pedidos)
    lidos = {p.rsplit("/", 1)[-1] for p in pedidos if p != lista}
    assert lidos == {
        "apoio-a-traducao",
        "premio-camoes-de-literatura",
        "premio-monteiro-lobato",
        "premio-literario-biblioteca-nacional-1",
    }
    assert r.descartes["nao_e_fomento"] == 3, (
        "Centro de Cooperação, Projeto Educativo e Acordos de cooperação"
    )


# ── configuração ─────────────────────────────────────────────────────────────────────────────


def test_fontes_govbr_do_catalogo_tem_termos_conferidos_e_formato_valido():
    govs = [f for f in carregar_fontes_v4(FONTES) if f.coletor == "govbr"]
    assert {f.id for f in govs} == {
        "funarte-editais",
        "iphan-editais",
        "palmares-editais",
        "bn-editais",
    }
    for f in govs:
        assert (
            f.ativa and f.tipo == "oficial" and f.formato in ("cards", "links", "volto") and f.orgao
        )
        assert f.termos_conferidos_em == "2026-11-08" and f.termos_url and f.robots_ok is True
        assert f.intervalo_min_entre_requisicoes_s >= 3 and f.url.startswith("https://www.gov.br/")
        assert f.formato != "links" or f.padrao_links
    assert next(f for f in govs if f.id == "iphan-editais").formato == "volto"


def test_vigia_da_funarte_com_endereco_morto_foi_desativado_com_motivo():
    v = next(f for f in carregar_fontes_v4(FONTES) if f.id == "vigia-funarte-editais")
    assert not v.ativa and "404" in v.motivo and "funarte-editais" in v.motivo


def test_coletor_govbr_esta_ligado_no_despacho_das_fontes():
    texto = (Path(__file__).resolve().parent.parent / "worker" / "fontes_v4.py").read_text(
        encoding="utf-8"
    )
    assert (
        'f.coletor == "govbr"' in texto and "govbr.coletar(cliente, f, intervalo, agora)" in texto
    )


def test_robots_do_govbr_documentado_no_yaml():
    texto = (Path(__file__).resolve().parent.parent / "worker" / "fontes.yaml").read_text(
        encoding="utf-8"
    )
    assert "Termos e robots conferidos em 08/11/2026" in texto and yaml.safe_load(texto)["fontes"]
