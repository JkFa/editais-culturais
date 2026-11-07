"""Fontes novas (v4): MinC, CultBR, Radar de Editais e vigias, com fixtures reais de 07/11/2026."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from worker.coletores import cultbr, minc, radar_emma, vigia
from worker.fila import normalizar_url
from worker.web import ClienteWeb

V4 = Path(__file__).parent / "fixtures" / "v4"
AGORA = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def ler(nome: str) -> str:
    return (V4 / nome).read_text(encoding="utf-8")


URL_ROUANET = (
    "https://www.gov.br/cultura/pt-br/assuntos/editais/inscricoes-abertas/"
    "programa-rouanet-nas-favelas-2/programa-rouanet-nas-favelas-2"
)


# ---------------------------------------------------------------- MinC


def test_minc_listagem_traz_os_4_cards_reais():
    itens = minc.parse_listagem(ler("minc_listagem.html"))
    assert len(itens) == 4
    titulos = [t for _, t in itens]
    assert "Programa Rouanet nas Favelas 2" in titulos
    assert all(u.startswith("https://www.gov.br/cultura/") for u, _ in itens)


def test_minc_edital_encerrado_e_descartado():
    bruto, cand, motivo = minc.converter_detalhe(
        URL_ROUANET, "x", ler("minc_detalhe_rouanet_favelas.html"), AGORA
    )
    assert bruto is None and cand is None and motivo == "encerrado"  # prazo 13/10/2026


def test_minc_edital_aberto_vira_edital_com_prazo_e_evidencia():
    quando = datetime(2026, 10, 1, tzinfo=UTC)
    bruto, cand, _ = minc.converter_detalhe(
        URL_ROUANET, "x", ler("minc_detalhe_rouanet_favelas.html"), quando
    )
    assert cand is None
    assert (
        bruto.fonte == "minc-inscricoes-abertas" and bruto.orgao_fomento == "Ministério da Cultura"
    )
    assert bruto.inscricao_fim == datetime(
        2026, 10, 14, 2, 59, 59, tzinfo=UTC
    )  # 13/10 23:59 em Brasília
    assert bruto.fim_tem_horario is False and bruto.abrangencia == "nacional"
    assert "13/10/2026" in bruto.proveniencia["inscricao_fim"].trecho
    assert bruto.url_inscricao == "https://salic.cultura.gov.br/autenticacao/index/index"
    assert "edital_de_chamamento_publico_minc_rouanet" in bruto.url_edital_pdf
    assert bruto.inscricao_inicio == datetime(2026, 8, 15, 3, 0, tzinfo=UTC)


def test_minc_ciclo_de_saberes_aberto():
    bruto, _, _ = minc.converter_detalhe(
        "https://www.gov.br/cultura/x/ciclo", "x", ler("minc_detalhe_ciclo_de_saberes.html"), AGORA
    )
    assert bruto.inscricao_fim == datetime(2026, 11, 9, 2, 59, 59, tzinfo=UTC)
    assert "8 de novembro de 2026" in bruto.proveniencia["inscricao_fim"].trecho


def test_minc_sem_prazo_legivel_vai_para_a_fila_e_nao_e_publicado():
    html = "<html><body><h1>Edital Teste A</h1><main><p>Texto fictício sem datas de inscrição.</p></main></body></html>"
    bruto, cand, motivo = minc.converter_detalhe("https://exemplo.invalid/a", "x", html, AGORA)
    assert bruto is None and motivo == "sem_prazo"
    assert cand.titulo == "Edital Teste A" and cand.link_oficial == "https://exemplo.invalid/a"
    assert "prazo" in cand.motivo


async def test_minc_coleta_ponta_a_ponta_com_transporte_simulado():
    paginas = {
        "/cultura/pt-br/assuntos/editais/inscricoes-abertas": ler("minc_listagem.html"),
    }
    detalhes = ler("minc_detalhe_ciclo_de_saberes.html")

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        html = paginas.get(req.url.path, detalhes)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    async def sem_espera(_: float) -> None:
        return None

    async with ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera) as c:
        res = await minc.coletar(
            c, "https://www.gov.br/cultura/pt-br/assuntos/editais/inscricoes-abertas", 0, AGORA
        )
    assert len(res.brutos) == 4  # o mesmo detalhe serve aos 4 links: todos abertos
    assert res.textos and all(t for t in res.textos.values())


# ---------------------------------------------------------------- CultBR


def itens_cultbr() -> list[dict]:
    return json.loads(ler("cultbr_abertos.json"))["items"]


def test_cultbr_converte_os_itens_estruturados_reais():
    convertidos = []
    for item in itens_cultbr():
        if item["json_oportunidade_mapas"]:
            bruto, motivo = cultbr.converter(item, AGORA)
            if bruto:
                convertidos.append(bruto)
    assert len(convertidos) == 12
    b = next(x for x in convertidos if x.id_externo == "64219")
    assert b.fonte == "cultbr-pnab" and b.via == "CultBR (MinC)"
    assert (
        b.orgao_fomento == "Município de Agrestina" and b.municipio == "Agrestina" and b.uf == "PE"
    )
    assert b.abrangencia == "municipal"
    assert b.inscricao_fim == datetime(
        2026, 11, 13, 2, 59, 59, tzinfo=UTC
    )  # 12/11 23:59 em Brasília
    assert float(b.valor_total) == 16500.0
    assert set(b.proponentes) == {"PF", "MEI", "PJ_COM_FINS", "PJ_SEM_FINS", "COLETIVO"}
    assert (
        b.proveniencia["valor_total"].via == "api"
        and "16500" in b.proveniencia["valor_total"].trecho
    )
    assert b.url_origem == "https://culteditais.cultura.gov.br/oportunidade/13103"
    assert b.url_edital_pdf.endswith(".pdf")


def test_cultbr_item_sem_json_nao_vira_edital():
    sem = next(i for i in itens_cultbr() if not i["json_oportunidade_mapas"])
    assert cultbr.converter(sem, AGORA) == (None, "sem_prazo_estruturado")


def test_cultbr_encerrado_e_descartado():
    item = next(i for i in itens_cultbr() if i["json_oportunidade_mapas"])
    depois = datetime(2027, 1, 1, tzinfo=UTC)
    assert cultbr.converter(item, depois) == (None, "encerrado")


def test_cultbr_nome_legivel():
    assert (
        cultbr.nome_legivel("MUNICIPIO DE SAO MIGUEL DE TAIPU")
        == "Município de Sao Miguel de Taipu"
    )


async def test_cultbr_coleta_dedup_na_fonte_e_limite_de_candidatos():
    dados = json.loads(ler("cultbr_abertos.json"))
    chamadas: list[str] = []

    def tratar(req: httpx.Request) -> httpx.Response:
        chamadas.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if "back.portal" in req.url.host:
            return httpx.Response(200, json={**dados, "total_pages": 1})
        return httpx.Response(
            200,
            text="<html><body><h1>Página oficial fictícia</h1><p>Sem prazo.</p></body></html>",
            headers={"content-type": "text/html"},
        )

    async def sem_espera(_: float) -> None:
        return None

    async with ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera) as c:
        res = await cultbr.coletar(
            c,
            "https://back.portal.cultbr.cultura.gov.br/v1/public/par/editais",
            0,
            set(),
            set(),
            AGORA,
        )
    assert len(res.brutos) == 12
    assert res.descartes["duplicado_na_fonte"] >= 1  # "Pontos e Pontões" aparece 2x na fonte real
    assert 0 < len(res.candidatos) <= 20
    assert all(c.origem.startswith("CultBR") for c in res.candidatos)


# ---------------------------------------------------------------- Radar de Editais (descoberta)


def test_radar_le_so_os_links_e_ignora_patrocinio_continuo():
    links = radar_emma.links_oficiais(ler("radar_emma_editais_bloco.js"))
    assert len(links) == 222 and all(x.startswith("http") for x in links)
    assert "https://www.claro.com.br/institucional/patrocinios" not in links  # tipo 'continuo'
    assert len(set(links)) == len(links)


async def test_radar_nao_copia_titulos_e_respeita_hosts_ignorados():
    html = f"<html><script>{ler('radar_emma_editais_bloco.js')}</script></html>"
    visitados: list[str] = []

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "radareditais.clubemma.com.br":
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        visitados.append(req.url.host)
        return httpx.Response(
            200,
            text="<html><body><h1>Página fictícia</h1><p>inscrições até 20/12/2027</p></body></html>",
            headers={"content-type": "text/html"},
        )

    async def sem_espera(_: float) -> None:
        return None

    async with ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera) as c:
        res = await radar_emma.coletar(
            c,
            "https://radareditais.clubemma.com.br/",
            0,
            set(),
            set(),
            limite=5,
            ignorar_hosts=(
                "prosas.com.br",
                "portal.cultbr.cultura.gov.br",
                "culteditais.cultura.gov.br",
            ),
        )
    assert res.brutos == []  # descoberta nunca publica
    assert len(res.candidatos) == 5 and res.descartes["limite_por_execucao"] > 0
    assert res.descartes["host_ignorado"] > 60  # Prosas e CultBR ficam de fora
    assert not any("prosas" in h for h in visitados)  # nenhuma requisição ao Prosas
    assert all("Radar de Editais (Clube Emma)" in c.origem for c in res.candidatos)
    assert all("(omitido)" not in c.titulo for c in res.candidatos)


async def test_radar_pula_links_ja_na_base_e_na_fila():
    html = f"<html><script>{ler('radar_emma_editais_bloco.js')}</script></html>"
    links = radar_emma.links_oficiais(ler("radar_emma_editais_bloco.js"))
    conhecido = next(x for x in links if "prosas" not in x and "cultbr" not in x)

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    async def sem_espera(_: float) -> None:
        return None

    async with ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera) as c:
        res = await radar_emma.coletar(
            c,
            "https://radareditais.clubemma.com.br/",
            0,
            {normalizar_url(conhecido)},
            set(),
            limite=0,
            ignorar_hosts=("prosas.com.br",),
        )
    assert res.descartes["ja_na_base"] >= 1


# ---------------------------------------------------------------- vigia de página


def texto_longo(extra: str = "") -> str:
    linhas = "".join(
        f"<p>Linha fictícia número {i} do edital de teste da página vigiada.</p>" for i in range(30)
    )
    return f"<html><body><main>{linhas}{extra}</main></body></html>"


async def vigiar(html: str, anterior):
    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    async def sem_espera(_: float) -> None:
        return None

    async with ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera) as c:
        return await vigia.vigiar(
            c, "vigia-teste", "Fonte Teste", "https://exemplo.invalid/p", 0, anterior, AGORA
        )


async def test_vigia_primeira_execucao_so_cria_linha_de_base():
    res = await vigiar(texto_longo(), None)
    assert res.candidatos == [] and res.estado["hash"] and res.detalhe == "linha de base criada"


async def test_vigia_pagina_igual_nao_abre_candidato():
    base = (await vigiar(texto_longo(), None)).estado
    res = await vigiar(texto_longo(), base)
    assert res.candidatos == [] and res.estado["hash"] == base["hash"]


async def test_vigia_mudanca_abre_candidato_com_diff_resumido():
    base = (await vigiar(texto_longo(), None)).estado
    res = await vigiar(
        texto_longo("<p>Chamada pública 2027: inscrições abertas de 01/02/2027 a 30/03/2027.</p>"),
        base,
    )
    assert len(res.candidatos) == 1
    c = res.candidatos[0]
    assert c.titulo == "A página de Fonte Teste mudou: possível edital novo"
    assert any("Chamada pública 2027" in x for x in c.diff) and c.diff[0].startswith("+ ")
    assert c.link_oficial == "https://exemplo.invalid/p" and "vigia" in c.origem


async def test_vigia_ruido_curto_nao_abre_candidato():
    base = (await vigiar(texto_longo(), None)).estado
    res = await vigiar(texto_longo("<p>Visitas: 1234567</p>"), base)
    assert res.candidatos == []


@pytest.mark.parametrize("fixture", ["vigia_bndes.html", "vigia_natura.html"])
async def test_vigia_pagina_feita_em_javascript_e_conteudo_insuficiente(fixture):
    res = await vigiar(ler(fixture), None)  # páginas reais de 07/11/2026
    assert res.status == "conteudo_insuficiente" and res.estado == {}


def test_linhas_novas_ignora_o_que_ja_existia():
    assert vigia.linhas_novas(["a" * 30], ["a" * 30, "b" * 30, "curta"]) == ["b" * 30]
