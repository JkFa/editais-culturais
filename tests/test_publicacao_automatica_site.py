"""Selo "Verificado automaticamente" no site: só nos editais publicados por programa, com aviso honesto e link de erro."""

import copy
import json
from urllib.parse import parse_qs, urlparse

from e2e_util import abertos, nova_pagina

TITULO_AUTO = "EDITAL DE TESTE PUBLICADO POR PROGRAMA"


def snapshot_com_auto(snapshot, ligada=True):
    corpo = copy.deepcopy(snapshot)
    base = next(e for e in corpo["editais"] if e.get("ambito", "brasil") == "brasil")
    auto = {
        **base,
        "chave": "auto-teste-1", "fonte": "publicacao-automatica", "id_externo": "auto-1", "titulo": TITULO_AUTO,
        "orgao_fomento": "Prefeitura Municipal de Exemplo", "uf": "SP", "via": None, "verificacao": "automatica", "verificado_em": None,
        "url_origem": "https://exemplo.sp.gov.br/edital", "fontes_alternativas": None,
        "proveniencia": {"inscricao_fim": {"via": "regex", "trecho": "inscrições de 01/11/2026 a 20/11/2026"}},
        "inscricao_fim": "2099-01-01T02:59:59+00:00",
    }  # fmt: skip
    for e in corpo["editais"]:
        e.setdefault("verificacao", None)
    corpo["editais"].append(auto)
    corpo["fontes"] = [f for f in corpo["fontes"] if f["fonte"] != "publicacao-automatica"] + [
        {"fonte": "publicacao-automatica", "nome": "Publicação automática", "tipo": "oficial", "metodo": "html", "url": "https://x", "ativa": ligada,
         "status": "ok" if ligada else "desativada", "erro": None, "motivo": None if ligada else "Desligada: nenhum edital é publicado sem uma pessoa conferir.",
         "ultima_execucao": None, "ultimo_sucesso": None, "total_coletado": 1}
    ]  # fmt: skip
    return corpo


def abrir(pg, url, corpo, caminho="editais/"):
    pg.route(
        "**/data/snapshot.json",
        lambda r: r.fulfill(content_type="application/json", body=json.dumps(corpo)),
    )
    pg.goto(url + caminho)
    pg.wait_for_selector("html[data-pronto]")


def test_so_o_edital_publicado_por_programa_ganha_o_selo(navegador, url, snapshot):
    pg = nova_pagina(navegador)
    abrir(pg, url, snapshot_com_auto(snapshot))
    cards = pg.locator(".card")
    assert cards.count() == len(abertos(snapshot)) + 1
    selos = pg.locator(".card .selo-auto")
    assert selos.count() == 1, "os outros editais não ganham selo"
    card = pg.locator(".card", has_text=TITULO_AUTO)
    assert "Verificado automaticamente" in card.locator(".selo-auto").inner_text()
    assert "Não foi conferido por uma pessoa" in card.locator(".selo-auto").get_attribute("title")
    assert pg.erros == []
    pg.close()


def test_painel_explica_o_selo_mostra_o_trecho_e_oferece_reportar_erro(navegador, url, snapshot):
    pg = nova_pagina(navegador)
    abrir(pg, url, snapshot_com_auto(snapshot))
    pg.locator(".card", has_text=TITULO_AUTO).locator("h3 button").click()
    painel = pg.locator(".painel")
    bloco = painel.locator(".verificacao-auto")
    assert (
        "verificado automaticamente" in bloco.inner_text().lower()
        and "Não foi conferido por uma pessoa" in bloco.inner_text()
    )
    link = bloco.get_by_role("link", name="Reportar erro neste edital")
    href = link.get_attribute("href")
    q = parse_qs(urlparse(href).query)
    assert href.startswith("https://github.com/JkFa/editais-culturais/issues/new?") and q[
        "labels"
    ] == ["erro-de-dado"]
    assert TITULO_AUTO in q["title"][0] and "https://exemplo.sp.gov.br/edital" in q["body"][0]
    assert link.get_attribute("rel") == "noopener noreferrer"
    painel.get_by_text("Como sabemos disso").click()
    assert "Lido do texto do edital (automático)" in painel.inner_text(), (
        "a proveniência não pode dizer 'curadoria humana'"
    )
    assert (
        "Conferido por curadoria humana" not in painel.inner_text()
        and "verificado em" not in painel.inner_text().lower()
    )
    assert pg.erros == []
    pg.close()


def test_edital_sem_selo_nao_mostra_bloco_nem_link_de_erro(navegador, url, snapshot):
    pg = nova_pagina(navegador)
    abrir(pg, url, snapshot_com_auto(snapshot))
    pg.locator(".card", has_not_text=TITULO_AUTO).first.locator("h3 button").click()
    assert pg.locator(".painel .verificacao-auto").count() == 0
    assert pg.locator(".painel").get_by_text("Reportar erro").count() == 0
    pg.close()


def test_sobre_so_explica_o_selo_enquanto_a_publicacao_automatica_estiver_ligada(
    navegador, url, snapshot
):
    pg = nova_pagina(navegador)
    abrir(pg, url, snapshot_com_auto(snapshot, ligada=True), "sobre/")
    secao = pg.locator("#publicacao-automatica")
    assert secao.is_visible()
    texto = secao.inner_text()
    assert (
        "sem que uma pessoa conferisse" in texto
        and "todo dia" in texto
        and "nunca “aprovado por uma pessoa”" in texto
    )
    assert pg.erros == []
    pg.close()
    pg2 = nova_pagina(navegador)
    abrir(pg2, url, snapshot_com_auto(snapshot, ligada=False), "sobre/")
    assert not pg2.locator("#publicacao-automatica").is_visible()
    assert "Desligada" in pg2.inner_text("#fontes-lista")
    pg2.close()


def test_selo_no_celular_cabe_e_nao_quebra_o_layout(navegador, url, snapshot):
    pg = nova_pagina(navegador, largura=360, altura=740)
    abrir(pg, url, snapshot_com_auto(snapshot))
    assert pg.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    assert pg.locator(".card", has_text=TITULO_AUTO).locator(".selo-auto").is_visible()
    pg.close()
