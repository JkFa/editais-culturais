"""v4 no site: filtro "Tipo de apoio", "via <plataforma>", requisitos e a lista de fontes em /sobre/."""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from e2e_util import abertos, nova_pagina


def pronta(pg, url, caminho):
    pg.goto(url + caminho)
    pg.wait_for_selector("html[data-pronto]")


def test_filtro_tipo_de_apoio_filtra_e_vai_para_a_url(navegador, url, snapshot):
    pg = nova_pagina(navegador)
    pronta(pg, url, "editais/")
    esperados = [e for e in abertos(snapshot) if e.get("tipo_apoio") == "cessao_de_espaco"]
    assert esperados, "o snapshot real tem os espaços da Funarte"
    pg.locator("#chips-apoio .chip", has_text="Cessão de espaço").click()
    assert pg.locator(".card").count() == len(esperados)
    assert parse_qs(urlparse(pg.url).query)["apoio"] == ["cessao_de_espaco"]
    pg.reload()
    pg.wait_for_selector("html[data-pronto]")
    assert (
        pg.locator("#chips-apoio .chip", has_text="Cessão de espaço").get_attribute("aria-pressed")
        == "true"
    )
    assert pg.locator(".card").count() == len(esperados)
    assert pg.erros == []
    pg.close()


def test_tipo_de_apoio_nulo_nao_aparece_sob_o_filtro(navegador, url, snapshot):
    pg = nova_pagina(navegador)
    pronta(pg, url, "editais/?apoio=credenciamento")
    com_tipo = [e for e in abertos(snapshot) if e.get("tipo_apoio") == "credenciamento"]
    assert pg.locator(".card").count() == len(com_tipo)
    pg.close()


def _snapshot_ficticio(snapshot):
    base = next(e for e in snapshot["editais"] if e["fonte"].startswith("mapas:"))
    um = {
        **base,
        "chave": "cultbr-pnab:teste-a",
        "fonte": "cultbr-pnab",
        "id_externo": "teste-a",
        "titulo": "Edital Teste A",
        "via": "CultBR (MinC)",
        "tipo_apoio": "patrocinio_incentivado",
        "requisitos": ["Exige projeto aprovado na Lei Rouanet"],
        "fontes_alternativas": [
            {
                "fonte": "mapas:ce",
                "url": "https://exemplo.invalid/alt",
                "via": "Mapas Culturais (CE)",
            }
        ],
    }
    return {**snapshot, "editais": [um]}


def test_card_e_painel_mostram_via_requisito_e_fontes_alternativas(navegador, url, snapshot):
    pg = nova_pagina(navegador)
    falso = _snapshot_ficticio(snapshot)
    pg.route(
        "**/data/snapshot.json",
        lambda r: r.fulfill(content_type="application/json", body=json.dumps(falso)),
    )
    pronta(pg, url, "editais/")
    card = pg.locator(".card").first
    assert "via CultBR (MinC)" in card.inner_text()
    assert card.locator(".pilula.requisito").inner_text() == "Exige projeto aprovado na Lei Rouanet"
    assert "Patrocínio incentivado" in card.inner_text()
    card.locator("h3 button").click()
    painel = pg.locator(".painel")
    painel.wait_for()
    texto = painel.inner_text()
    assert "via CultBR (MinC)" in texto and "Também publicado em".lower() in texto.lower()
    assert (
        painel.get_by_role("link", name="Mapas Culturais (CE)").get_attribute("href")
        == "https://exemplo.invalid/alt"
    )
    pg.close()


def test_sobre_lista_as_fontes_por_tipo_com_saude_e_motivo(navegador, url, snapshot):
    pg = nova_pagina(navegador)
    pronta(pg, url, "sobre/")
    pg.wait_for_selector("#fontes-lista h3")
    titulos = pg.locator("#fontes-lista h3").all_inner_texts()
    assert [t.lower() for t in titulos][:2] == ["oficiais", "plataformas"]
    assert any("descoberta" in t.lower() for t in titulos)
    ativas = sum(1 for f in snapshot["fontes"] if f["ativa"])
    assert re.search(rf"{ativas} fontes ativas", pg.inner_text("#fontes-resumo"))
    prosas = pg.locator(".fonte-item", has_text="Prosas")
    assert "desativada" in prosas.inner_text() and "extração de dados" in prosas.inner_text()
    assert pg.locator(".fonte-item", has_text="Ministério da Cultura").count() >= 1
    corpo = pg.inner_text("#fontes")  # âncora da seção (o h2)
    assert corpo == "Fontes"
    pagina = pg.inner_text("main")
    assert (
        "Usamos agregadores só para descobrir editais. Os dados exibidos vêm sempre da fonte oficial"
        in pagina
    )
    for link in pg.locator("#fontes-lista a").all():
        assert link.get_attribute("href").startswith("https://")
    assert pg.erros == []
    pg.close()


def test_sobre_mostra_o_placar_de_cobertura_com_nota_metricas_e_tabela_por_uf(navegador, url):
    cobertura = json.loads(
        (Path(__file__).resolve().parent.parent / "web" / "data" / "cobertura.json").read_text(
            encoding="utf-8"
        )
    )
    pg = nova_pagina(navegador)
    pronta(pg, url, "sobre/")
    pg.wait_for_selector("#cobertura-metricas li")
    nota = pg.inner_text("#cobertura-nota .nota-grande")
    assert nota.replace(",", ".").startswith(f"{cobertura['nota']:g}") and nota.endswith("de 10")
    itens = pg.locator("#cobertura-metricas li")
    assert itens.count() == 8
    recall = itens.filter(has_text="Recall auditado")
    assert "não medido" in recall.inner_text() and "0,0 de 2,5" in recall.inner_text()
    assert itens.first.locator('[role="img"]').get_attribute("aria-label").endswith("% da meta")
    pg.locator("#cobertura-uf-bloco summary").click()
    linhas = pg.locator("#cobertura-uf tbody tr")
    assert linhas.count() == 27
    ce = linhas.filter(has_text="CE · Ceará")
    assert "tem" in ce.inner_text() and "Fortaleza" in ce.inner_text()
    assert pg.locator("#cobertura-uf caption").count() == 1
    assert "Enquanto não houver auditoria" in pg.text_content("main")  # dentro de <details> fechado
    assert pg.erros == []
    pg.close()
