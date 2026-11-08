"""Coletor On the Move com feeds reais de 07/11/2026 (fixtures) e sem acessar a internet."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from worker.coletores import onthemove
from worker.padronizacao import padronizar
from worker.web import ClienteWeb

FIX = Path(__file__).parent / "fixtures" / "internacional"
AGORA = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
BASE = "https://on-the-move.org"


def ler(nome: str) -> str:
    return (FIX / nome).read_text(encoding="utf-8")


def mapa_de_respostas(extra: dict[str, httpx.Response] | None = None):
    """Transporte falso: robots.txt vazio e um XML por URL de feed."""
    urls = {
        onthemove.url_do_feed(BASE): "otm_open_calls.xml",
        onthemove.url_do_feed(BASE, tipo=7): "otm_cat_7.xml",
        onthemove.url_do_feed(BASE, tipo=4): "otm_cat_4.xml",
        onthemove.url_do_feed(BASE, tipo=8): "otm_cat_8.xml",
        onthemove.url_do_feed(BASE, tipo=81): "otm_cat_81.xml",
        onthemove.url_do_feed(BASE, disciplina=38): "otm_disc_38.xml",
        onthemove.url_do_feed(BASE, disciplina=30): "otm_disc_30.xml",
        onthemove.url_do_feed(BASE, online=49): "otm_online_49.xml",
    }
    pedidos: list[str] = []

    def responder(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        pedidos.append(url)
        if url.endswith("/robots.txt"):
            return httpx.Response(404)
        if extra and url in extra:
            return extra[url]
        if url in urls:
            return httpx.Response(
                200, text=ler(urls[url]), headers={"content-type": "application/rss+xml"}
            )
        vazio = '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
        return httpx.Response(200, text=vazio, headers={"content-type": "application/rss+xml"})

    return httpx.MockTransport(responder), pedidos


async def _sem_espera(_: float) -> None:
    return None


async def coletar(extra=None, agora=AGORA):
    transporte, pedidos = mapa_de_respostas(extra)
    async with ClienteWeb(transporte=transporte, dormir=_sem_espera) as cliente:
        return await onthemove.coletar(cliente, BASE, 0, agora), pedidos


def por_id(res, trecho: str):
    return next(b for b in res.brutos if trecho in b.id_externo)


def test_url_do_feed_segue_o_padrao_do_site():
    assert onthemove.url_do_feed(BASE) == f"{BASE}/news/all/all/all/all/all/all/feed"
    assert onthemove.url_do_feed(BASE, tipo=7) == f"{BASE}/news/7/all/all/all/all/all/feed"
    assert onthemove.url_do_feed(BASE, online=49) == f"{BASE}/news/all/all/49/all/all/all/feed"
    assert onthemove.url_do_feed(BASE, disciplina=38) == f"{BASE}/news/all/all/all/all/38/all/feed"


@pytest.mark.parametrize(
    "titulo, esperado",
    [
        ("Festival Opera Prima 2027 (Italy)", "IT"),
        ("Dirtly Lab Residencies 2027 (Belgium, Croatia, Germany)", "BE,HR,DE"),
        ("Programa (United States of America)", "US"),
        ("IN SITU: Online Support for Emerging Artists (3rd call)", None),
        ("Chamada sem país no título", None),
        ("Chamada (Atlantis)", None),
    ],
)
def test_pais_do_titulo(titulo, esperado):
    assert onthemove.paises_do_titulo(titulo) == esperado


async def test_coleta_traz_as_5_chamadas_com_tipo_disciplina_prazo_e_fonte():
    res, pedidos = await coletar()
    assert res.status == "ok" and len(res.brutos) == 5 and not res.descartes
    opera = por_id(res, "festival-opera-prima")
    assert opera.fonte == "onthemove" and opera.ambito == "internacional"
    assert opera.tipo_oportunidade == "apresentacao"  # feed do tipo "Presenting Work"
    assert opera.linguagens == ["Teatro & Dança"] and opera.pais_destino == "IT"
    assert opera.idioma_original == "en" and opera.titulo_original == opera.titulo
    assert opera.via == "On the Move" and opera.url_origem.startswith(BASE + "/news/")
    assert opera.url_inscricao and "operaprima" in opera.url_inscricao
    # 21/11/2026, só data: vale até 23:59:59 em Brasília e não afirma horário
    assert opera.inscricao_fim == datetime(2026, 11, 22, 2, 59, 59, tzinfo=UTC)
    assert opera.fim_tem_horario is False
    assert opera.prazo_original == datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    assert "Deadline" in opera.proveniencia["inscricao_fim"].trecho
    # texto da chamada: cobre hospedagem e um jantar, orçamento de produção que cobre viagem e
    # refeições, e "the agreed fee will be paid"
    assert opera.custos_cobertos == ["viagem", "hospedagem", "cache", "producao", "alimentacao"]
    assert len(opera.resumo_original) <= onthemove.LIMITE_TRECHO  # trecho, nunca o texto integral
    # um pedido por feed, sem repetir (cortesia com o site)
    assert len(pedidos) == len(set(pedidos))


async def test_user_agent_identifica_o_portal_sem_email():
    transporte, _ = mapa_de_respostas()
    async with ClienteWeb(
        repo_url="https://github.com/JkFa/editais-culturais",
        transporte=transporte,
        dormir=_sem_espera,
    ) as c:
        assert "editais-culturais-bot" in c._ua and "github.com/JkFa/editais-culturais" in c._ua
        assert "@" not in c._ua


async def test_elegibilidade_aberto_so_quando_o_texto_diz():
    res, _ = await coletar()
    frankfurt = por_id(res, "frankfurt-moves")
    assert frankfurt.elegibilidade_br == "aberto"  # "trained in Africa, Asia, Latin America..."
    assert "latin america" in frankfurt.proveniencia["elegibilidade_br"].trecho
    assert frankfurt.tipo_oportunidade == "residencia" and frankfurt.pais_destino == "DE"
    # as outras quatro não dizem quem pode se candidatar: verificar, sem evidência inventada.
    # (o Dirtly Lab fala em "instituições parceiras" anfitriãs: isso não é exigência de parceiro)
    for outro in ("festival-opera-prima", "dirtly-lab", "bernheim", "situ-online"):
        assert por_id(res, outro).elegibilidade_br == "verificar", outro
        assert "elegibilidade_br" not in por_id(res, outro).proveniencia


async def test_tipos_linguagens_e_online():
    res, _ = await coletar()
    assert por_id(res, "bernheim").tipo_oportunidade == "residencia"
    assert por_id(res, "bernheim").linguagens == [
        "Teatro & Dança"
    ]  # Multiáreas some se há específica
    assert por_id(res, "frankfurt-moves").linguagens == ["Teatro & Dança"]
    assert set(por_id(res, "dirtly-lab").linguagens) == {"Multiáreas"}
    situ = por_id(res, "situ-online")
    assert situ.tipo_oportunidade == "formacao"  # feed "Training"
    assert situ.pais_destino == "Online"  # sem país no título e na faceta "Online or Remote"
    assert por_id(res, "dirtly-lab").pais_destino == "BE,HR,DE,RS,SI,ES"
    assert por_id(res, "situ-online").custos_cobertos == ["viagem", "hospedagem", "alimentacao"]


async def test_chamada_vencida_e_descartada():
    res, _ = await coletar(agora=datetime(2026, 11, 25, tzinfo=UTC))  # depois de 21/11
    ids = {b.id_externo for b in res.brutos}
    assert not any("festival-opera-prima" in i for i in ids)
    assert res.descartes["encerrado"] == 1 and len(res.brutos) == 4


async def test_sem_prazo_tenta_a_pagina_uma_vez_e_descarta_se_nao_achar():
    sem_prazo = ler("otm_open_calls.xml").replace("Deadline:", "Prazo:")
    pagina = "<html><body>sem nenhuma data aqui</body></html>"
    extra = {
        onthemove.url_do_feed(BASE): httpx.Response(
            200, text=sem_prazo, headers={"content-type": "application/rss+xml"}
        ),
    }
    transporte, pedidos = mapa_de_respostas(extra)

    def com_paginas(req: httpx.Request) -> httpx.Response:
        if "/news/" in str(req.url) and not str(req.url).endswith("/feed"):
            pedidos.append(str(req.url))
            return httpx.Response(200, text=pagina)
        return transporte.handler(req)  # type: ignore[attr-defined]

    async with ClienteWeb(
        transporte=httpx.MockTransport(com_paginas), dormir=_sem_espera
    ) as cliente:
        res = await onthemove.coletar(cliente, BASE, 0, AGORA)
    assert res.brutos == [] and res.descartes["sem_prazo"] == 5
    paginas = [p for p in pedidos if not p.endswith("/feed") and not p.endswith("robots.txt")]
    assert len(paginas) == 5 and len(set(paginas)) == 5  # uma tentativa por chamada


async def test_uma_faceta_que_falha_nao_derruba_a_coleta():
    extra = {onthemove.url_do_feed(BASE, tipo=7): httpx.Response(500)}
    res, _ = await coletar(extra)
    assert len(res.brutos) == 5 and "tipo" in (res.detalhe or "")
    assert por_id(res, "frankfurt-moves").tipo_oportunidade is None  # tipo não confirmado: vazio
    assert por_id(res, "festival-opera-prima").tipo_oportunidade == "apresentacao"


async def test_padronizar_internacional_nao_aplica_regras_em_portugues():
    res, _ = await coletar()
    edital = padronizar(por_id(res, "festival-opera-prima"), AGORA)
    assert edital.ambito == "internacional" and edital.abrangencia is None and edital.uf is None
    assert edital.valor_total is None and edital.proponentes is None
    assert edital.valor_brl is None and edital.resumo_pt is None
    assert edital.hash_conteudo and edital.chave.startswith("onthemove:")


# ---------------------------------------------------------------- orquestração e Mapas ibero-americano


async def test_falha_do_on_the_move_nao_derruba_outra_fonte_nem_a_coleta():
    from worker.catalogo import FonteConfig
    from worker.fontes_v4 import rodar_fontes_v4

    def fonte(id_, url):
        return FonteConfig(
            id=id_, nome=id_, tipo="plataforma", metodo="rss", coletor="onthemove", url=url,
            intervalo_min_entre_requisicoes_s=0,
        )  # fmt: skip

    transporte, _ = mapa_de_respostas()

    def responder(req: httpx.Request) -> httpx.Response:
        if req.url.host == "fora-do-ar.invalid":
            return httpx.Response(503)
        return transporte.handler(req)  # type: ignore[attr-defined]

    async with ClienteWeb(transporte=httpx.MockTransport(responder), dormir=_sem_espera) as cliente:
        out = await rodar_fontes_v4(
            [fonte("otm-ok", BASE), fonte("otm-quebrada", "https://fora-do-ar.invalid")],
            AGORA, set(), set(), {}, {}, cliente=cliente,
        )  # fmt: skip
    status = {s.fonte: s.status for s in out.status}
    assert status == {"otm-ok": "ok", "otm-quebrada": "erro"}
    assert len(out.resultados["otm-ok"].brutos) == 5
    assert [f["fonte"] for f in out.falhas] == ["otm-quebrada"]


def test_fontes_yaml_registra_onthemove_ativo_e_os_programas_inativos_com_motivo():
    from worker.catalogo import carregar_fontes_v4
    from worker.scraper_worker import FONTES, carregar_fontes

    fontes = {f.id: f for f in carregar_fontes_v4(FONTES)}
    assert fontes["onthemove"].ativa and fontes["onthemove"].coletor == "onthemove"
    for id_ in ("ibermusicas", "ibermedia", "iberescena", "ibermuseos"):
        assert not fontes[id_].ativa and len(fontes[id_].motivo) > 40, id_
    instancias, _ = carregar_fontes()
    ibero = next(i for i in instancias if i.slug == "iberculturaviva")
    assert ibero.ambito == "internacional" and ibero.ativa and ibero.base_url.startswith("https://")


def test_mapa_iberculturaviva_vira_internacional_e_descarta_o_que_nao_e_edital_aberto():
    import json
    from collections import Counter

    from worker.coletores.mapas_culturais import converter
    from worker.scraper_worker import carregar_fontes

    instancias, params = carregar_fontes()
    ibero = next(i for i in instancias if i.slug == "iberculturaviva")
    itens = json.loads((FIX / "mapas_mapa.iberculturaviva.org.json").read_text(encoding="utf-8"))
    antes_do_prazo = datetime(2026, 1, 10, tzinfo=UTC)  # a chamada 587 estava aberta nesta data
    descartes: Counter[str] = Counter()
    brutos = [converter(i, ibero, params, antes_do_prazo, descartes) for i in itens]
    abertos = [b for b in brutos if b]
    assert len(abertos) == 1
    bruto, texto = abertos[0]
    assert bruto.fonte == "mapas:iberculturaviva" and bruto.ambito == "internacional"
    assert bruto.pais_destino == "Ibero-América" and bruto.idioma_original == "es"
    assert bruto.elegibilidade_br == "verificar"  # não diz quem pode; nada de "aberto" por palpite
    assert bruto.titulo_original == bruto.titulo and bruto.prazo_original == bruto.inscricao_fim
    assert descartes["tipo:Festival"] == 1 and descartes["tipo:Concurso"] == 1  # só "Edital" vale
    assert descartes["encerrado"] == 1  # edital de 2025
    edital = padronizar(bruto, antes_do_prazo, texto)
    assert edital.ambito == "internacional" and edital.abrangencia is None


def test_instancias_brasileiras_continuam_sem_campos_internacionais():
    from worker.scraper_worker import carregar_fontes

    for i in carregar_fontes()[0]:
        if i.slug != "iberculturaviva":
            assert i.ambito == "brasil" and i.pais_destino is None
