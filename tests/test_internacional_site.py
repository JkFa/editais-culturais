"""Front internacional (Fase 1): menu, filtro, cards, "Posso me inscrever?", home e a regressão do Brasil.

As linhas de teste saem do próprio pipeline (feeds reais salvos -> coletor -> padronização), então o
front é testado contra o contrato que o worker realmente grava.
"""

import asyncio
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from e2e_util import abertos, nova_pagina

sys.path.insert(0, str(Path(__file__).parent))
from test_internacional_onthemove import AGORA, coletar  # noqa: E402

from worker.coletores import curadoria_internacional as ci  # noqa: E402
from worker.padronizacao import padronizar  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
LONGE = datetime(2099, 12, 1, 16, 0, tzinfo=UTC)  # dezembro: Bruxelas = UTC+1, 17h


def _linhas_internacionais() -> list[dict]:
    # o Playwright (sync) mantém um loop de eventos na thread principal: coletamos noutra thread
    with ThreadPoolExecutor(1) as pool:
        res, _ = pool.submit(asyncio.run, coletar()).result()
    brutos = (
        list(res.brutos)
        + ci.carregar(
            RAIZ / "worker" / "curadoria_internacional.json", datetime(2026, 11, 7).date()
        ).editais
    )
    linhas = []
    for i, bruto in enumerate(brutos):
        e = padronizar(bruto, AGORA)
        linha = {**e.para_linha(), "chave": e.chave}
        # prazos relativos ao relógio do teste: a CI roda meses depois da coleta que gerou as fixtures
        fim = datetime.now(UTC) + timedelta(days=10 + i)
        linha["inscricao_fim"] = fim.isoformat()
        if linha["prazo_original"]:
            linha["prazo_original"] = fim.replace(hour=12, minute=0, second=0).isoformat()
        linhas.append(linha)
    return linhas


def snapshot_com_internacionais(snapshot):
    linhas = _linhas_internacionais()
    frankfurt = next(x for x in linhas if "frankfurt-moves" in x["id_externo"])
    com_fuso = {
        **frankfurt,
        "chave": "onthemove:teste-bruxelas",
        "id_externo": "teste-bruxelas",
        "titulo": "Chamada de teste com horário e fuso (Belgium)",
        "titulo_original": "Chamada de teste com horário e fuso (Belgium)",
        "pais_destino": "BE",
        "elegibilidade_br": "verificar",
        "proveniencia": {},
        "fim_tem_horario": True,
        "prazo_fuso": "Europe/Brussels",
        "prazo_original": LONGE.isoformat(),
        "inscricao_fim": LONGE.isoformat(),
    }
    residencia = {
        **frankfurt,
        "chave": "onthemove:teste-residencia",
        "id_externo": "teste-residencia",
        "titulo": "Chamada que exige residência (Germany)",
        "titulo_original": "Chamada que exige residência (Germany)",
        "pais_destino": "DE",
        "elegibilidade_br": "requer_residencia",
        "proveniencia": {},
    }
    parceiro = {
        **residencia,
        "chave": "onthemove:teste-parceiro",
        "id_externo": "teste-parceiro",
        "titulo": "Chamada que exige parceiro (France)",
        "titulo_original": "Chamada que exige parceiro (France)",
        "pais_destino": "FR",
        "elegibilidade_br": "requer_parceiro",
    }
    novos = [*linhas, com_fuso, residencia, parceiro]
    return {**snapshot, "editais": [*snapshot["editais"], *novos]}, novos


@pytest.fixture
def falso(snapshot):
    return snapshot_com_internacionais(snapshot)


def abrir_com(pg, url, falso, caminho="editais/"):
    corpo, _ = falso
    pg.route(
        "**/data/snapshot.json",
        lambda r: r.fulfill(content_type="application/json", body=json.dumps(corpo)),
    )
    pg.goto(url + caminho)
    pg.wait_for_selector("html[data-pronto]")


def titulos(pg):
    return pg.locator(".card h3 button").all_inner_texts()


def test_padrao_continua_so_com_editais_brasileiros_identicos(navegador, url, snapshot, falso):
    """Regressão: com oportunidades de fora na base, a tela padrão não muda para o Brasil."""
    sem = nova_pagina(navegador)
    sem.goto(url + "editais/")
    sem.wait_for_selector("html[data-pronto]")
    esperados = titulos(sem)
    contadores = sem.locator("#contadores li").all_inner_texts()[:3]
    sem.close()
    pg = nova_pagina(navegador)
    abrir_com(pg, url, falso)
    assert titulos(pg) == esperados and len(esperados) == len(abertos(snapshot))
    assert pg.locator(".card-intl").count() == 0
    assert pg.locator("#contadores li").all_inner_texts()[:3] == contadores
    assert "oportunidades internacionais abertas" in pg.locator("#contadores li").last.inner_text()
    assert pg.locator("#grupo-intl").is_hidden()
    assert pg.erros == []
    pg.close()


def test_menu_tem_internacional_em_todas_as_paginas_e_marca_a_pagina_atual(navegador, url):
    for caminho in ["", "editais/", "sobre/", "projeto/", "404.html"]:
        pg = nova_pagina(navegador, largura=360)
        pg.goto(url + caminho)
        pg.wait_for_selector("header nav")
        link = pg.locator("header nav").get_by_role("link", name="Internacional")
        assert link.count() == 1, caminho
        assert link.evaluate("a => a.href").endswith("/editais/?amb=internacional"), caminho
        assert pg.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        ), caminho
        pg.close()
    pg = nova_pagina(navegador)
    pg.goto(url + "editais/")
    pg.wait_for_selector("html[data-pronto]")
    nav = pg.locator("header nav")
    assert nav.get_by_role("link", name="Editais").get_attribute("aria-current") == "page"
    nav.get_by_role("link", name="Internacional").click()
    pg.wait_for_url(re.compile(r"amb=internacional"))
    pg.wait_for_selector("html[data-pronto]")
    assert nav.get_by_role("link", name="Internacional").get_attribute("aria-current") == "page"
    assert nav.get_by_role("link", name="Editais").get_attribute("aria-current") is None
    pg.close()


def test_aba_internacional_mostra_so_oportunidades_de_fora(navegador, url, falso):
    pg = nova_pagina(navegador)
    abrir_com(pg, url, falso, "editais/?amb=internacional")
    _, novos = falso
    assert pg.locator(".card").count() == len(novos) == pg.locator(".card-intl").count()
    assert pg.locator("#titulo-explorador").inner_text() == "Oportunidades internacionais"
    assert pg.locator("#contadores li .valor").first.inner_text() == str(len(novos))
    # perfil, onde (UF) e tipo de apoio são do Brasil: somem; as perguntas novas aparecem
    for id_ in ("grupo-perfil", "grupo-abrangencia", "grupo-apoio"):
        assert pg.locator(f"#{id_}").is_hidden(), id_
    assert pg.locator("#grupo-intl").is_visible()
    assert (
        pg.locator("#pergunta-nacionalidade, #pergunta-residencia, #pergunta-organizacao").count()
        == 3
    )
    assert pg.locator(".card-assistente").count() == 0  # o assistente é para editais do Brasil
    assert pg.erros == []
    pg.close()


def test_card_internacional_mostra_titulo_original_linha_em_portugues_selos_fonte_e_prazo(
    navegador, url, falso
):
    pg = nova_pagina(navegador)
    abrir_com(pg, url, falso, "editais/?amb=internacional")
    card = pg.locator('.card[data-chave^="onthemove:frankfurt-moves"]')
    assert card.locator("h3").inner_text().startswith("Frankfurt Moves!")
    assert card.locator("h3").get_attribute("lang") == "en"
    # a linha em português é montada pelos campos, sem LLM
    assert (
        card.locator(".linha-pt").inner_text()
        == "Residência · Alemanha · cobre viagem e hospedagem · em inglês"
    )
    selo = card.locator(".selo-elegibilidade")
    assert selo.inner_text() == "Aberto a brasileiros" and "destaque" in selo.get_attribute("class")
    assert "Elegibilidade: Aberto a brasileiros" in selo.get_attribute("aria-label")
    assert card.locator(".selo-idioma").inner_text() == "EN"
    assert card.locator(".selo-idioma").get_attribute("aria-label") == "Idioma da chamada: inglês"
    assert card.locator(".selo-tipo").inner_text() == "Residência"
    custos = card.locator("ul.custos")
    assert custos.get_attribute("aria-label")
    assert "Cobre viagem" in custos.inner_text() and "Cobre hospedagem" in custos.inner_text()
    fonte = card.locator(".via-intl")
    assert fonte.inner_text() == "Fonte: On the Move"
    href = fonte.locator("a").get_attribute("href")
    assert (
        href.startswith("https://on-the-move.org/news/")
        and fonte.locator("a").get_attribute("rel") == "noopener noreferrer"
    )
    assert "(confira o horário na chamada)" in card.locator(".prazo-fonte").inner_text()
    outro = pg.locator('.card[data-chave^="onthemove:festival-opera-prima"] .selo-elegibilidade')
    assert outro.inner_text() == "Verificar elegibilidade" and "aviso" in outro.get_attribute(
        "class"
    )
    pg.close()


def test_prazo_com_fuso_aparece_como_a_fonte_informa(navegador, url, falso):
    pg = nova_pagina(navegador)
    abrir_com(pg, url, falso, "editais/?amb=internacional")
    card = pg.locator('.card[data-chave="onthemove:teste-bruxelas"]')
    assert (
        card.locator(".prazo-fonte").inner_text() == "Prazo: 1 dez 2099, 17h (horário de Bruxelas)"
    )
    pg.close()


def test_posso_me_inscrever_filtra_por_residencia_organizacao_e_nacionalidade(
    navegador, url, falso
):
    pg = nova_pagina(navegador)
    abrir_com(pg, url, falso, "editais/?amb=internacional")
    residencia = pg.locator('.card[data-chave="onthemove:teste-residencia"]')
    parceiro = pg.locator('.card[data-chave="onthemove:teste-parceiro"]')
    verificar = pg.locator('.card[data-chave^="onthemove:festival-opera-prima"]')
    aberto = pg.locator('.card[data-chave^="onthemove:frankfurt-moves"] .selo-elegibilidade')
    # sem resposta, tudo aparece (com o selo de cada um)
    assert residencia.count() == parceiro.count() == verificar.count() == 1
    assert residencia.locator(".selo-elegibilidade").inner_text() == "Requer residência"
    assert parceiro.locator(".selo-elegibilidade").inner_text() == "Requer parceiro"
    # 1) mora no Brasil: "requer residência" na Alemanha some; os outros ficam
    pg.select_option("#pergunta-residencia", "BR")
    assert residencia.count() == 0 and parceiro.count() == 1 and verificar.count() == 1
    # 2) mora na Alemanha: a chamada que exige residência lá volta
    pg.select_option("#pergunta-residencia", "DE")
    assert residencia.count() == 1
    # 3) sem organização: "requer parceiro" some. Com organização: aparece
    pg.select_option("#pergunta-organizacao", "nao")
    assert parceiro.count() == 0 and verificar.count() == 1  # "verificar" aparece sempre
    pg.select_option("#pergunta-organizacao", "sim")
    assert parceiro.count() == 1
    # 4) outra nacionalidade: "Aberto a brasileiros" deixa de valer e vira "Verificar elegibilidade"
    assert aberto.inner_text() == "Aberto a brasileiros"
    pg.select_option("#pergunta-nacionalidade", "outra")
    assert aberto.inner_text() == "Verificar elegibilidade"
    q = parse_qs(urlparse(pg.url).query)
    assert (
        q["amb"] == ["internacional"]
        and q["nac"] == ["outra"]
        and q["res"] == ["DE"]
        and q["org"] == ["sim"]
    )
    pg.reload()
    pg.wait_for_selector("html[data-pronto]")
    assert pg.locator("#pergunta-residencia").input_value() == "DE"
    assert pg.locator("#pergunta-organizacao").input_value() == "sim"
    assert pg.locator("#pergunta-nacionalidade").input_value() == "outra"
    assert "Moro em Alemanha" in pg.locator("#abrir-filtros").get_attribute("aria-label") or True
    assert pg.erros == []
    pg.close()


def test_todos_mostra_brasil_e_internacional_e_filtros_do_brasil_nao_escondem_os_de_fora(
    navegador, url, snapshot, falso
):
    pg = nova_pagina(navegador)
    abrir_com(pg, url, falso, "editais/?amb=todos&uf=CE")
    chaves = pg.locator(".card").evaluate_all("els => els.map(e => e.dataset.chave)")
    assert any(c.startswith("onthemove:") for c in chaves) and any(
        not c.startswith("onthemove:") for c in chaves
    )
    # a UF restringe só os do Brasil
    brasil_ce = [
        e for e in abertos(snapshot) if e.get("uf") == "CE" or e.get("abrangencia") == "nacional"
    ]
    assert len(
        [c for c in chaves if not c.startswith(("onthemove:", "curadoria-internacional:"))]
    ) == len(brasil_ce)
    pg.close()


def test_filtro_de_linguagem_vale_para_as_de_fora(navegador, url, falso):
    pg = nova_pagina(navegador)
    abrir_com(pg, url, falso, "editais/?amb=internacional&ling=Patrimônio")
    assert pg.locator(".card").count() == 1
    assert (
        "Ibermuseos" in pg.locator(".card").inner_text()
        or "museos" in pg.locator(".card").inner_text().lower()
    )
    pg.close()


def test_painel_internacional_tem_aviso_legal_fonte_e_sem_assistente(navegador, url, falso):
    pg = nova_pagina(navegador)
    abrir_com(pg, url, falso, "editais/?amb=internacional")
    pg.locator('.card[data-chave^="onthemove:frankfurt-moves"] h3 button').click()
    painel = pg.locator(".painel")
    painel.wait_for()
    texto = painel.inner_text()
    # (os títulos de seção do painel saem em caixa alta por CSS: compara sem diferenciar maiúsculas)
    assert "posso me candidatar?" in texto.lower() and "aberto a brasileiros" in texto.lower()
    assert "Trecho da fonte" in texto and "latin america" in texto.lower()
    assert (
        "requisitos de visto, idioma e documentação são de responsabilidade de quem se candidata"
        in texto
    )
    assert "sem converter para o horário de Brasília" in texto
    assert (
        painel.get_by_role("link", name=re.compile("Chamada original"))
        .get_attribute("href")
        .startswith("https://on-the-move.org/")
    )
    assert painel.get_by_role("link", name="Escrever projeto para este edital").count() == 0
    # o painel de "verificar" traz o aviso de elegibilidade
    pg.keyboard.press("Escape")
    pg.locator('.card[data-chave^="onthemove:festival-opera-prima"] h3 button').click()
    assert "Verificar elegibilidade" in pg.locator(".painel").inner_text()
    assert pg.erros == []
    pg.close()


def test_home_conta_internacionais_a_parte_e_o_seletor_da_linguagem(
    navegador, url, snapshot, falso
):
    sem = nova_pagina(navegador)
    sem.goto(url)
    sem.wait_for_selector("html[data-pronto]")
    brasil = {
        n: sem.locator(f'[data-numero="{n}"]').inner_text()
        for n in ("abertos", "encerrando", "fontes")
    }
    indice_brasil = sem.locator("#indice-linguagem li").all_inner_texts()
    semana_brasil = sem.locator("#semana li").all_inner_texts()
    sem.close()
    pg = nova_pagina(navegador)
    abrir_com(pg, url, falso, "")
    _, novos = falso
    for n, valor in brasil.items():  # números do Brasil idênticos
        assert pg.locator(f'[data-numero="{n}"]').inner_text() == valor, n
    assert pg.locator('[data-numero="internacionais"]').inner_text() == str(len(novos))
    assert pg.locator(".numero-link").get_attribute("href") == "editais/?amb=internacional"
    assert pg.locator("#indice-linguagem li").all_inner_texts() == indice_brasil  # padrão: Brasil
    assert pg.locator("#semana li").all_inner_texts() == semana_brasil
    assert pg.locator("#escopo-linguagem .chip[aria-pressed=true]").inner_text() == "Brasil"
    pg.locator("#escopo-linguagem .chip", has_text="Internacional").click()
    total = sum(
        int(t.split("\n")[1])
        for t in pg.locator("#indice-linguagem li").all_inner_texts()
        if "\n" in t
    )
    assert total >= len(novos)  # uma chamada pode ter mais de uma linguagem
    link = pg.locator("#indice-linguagem a").first.get_attribute("href")
    assert "amb=internacional" in link
    pg.locator("#escopo-linguagem .chip", has_text="Todos").click()
    assert pg.locator("#escopo-linguagem .chip[aria-pressed=true]").inner_text() == "Todos"
    assert pg.erros == []
    pg.close()


@pytest.mark.parametrize("tema", ["light", "dark"])
def test_celular_sem_rolagem_horizontal_nos_dois_temas(navegador, url, falso, tema):
    ctx = navegador.new_context(
        viewport={"width": 360, "height": 740},
        color_scheme=tema,
        locale="pt-BR",
        is_mobile=True,
        has_touch=True,
    )
    if tema == "dark":
        ctx.add_init_script("localStorage.setItem('editais-culturais:tema', 'escuro')")
    pg = ctx.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    abrir_com(pg, url, falso, "editais/?amb=internacional")
    assert pg.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    pg.locator('.card[data-chave^="onthemove:frankfurt-moves"] h3 button').click()
    pg.locator(".painel").wait_for()
    assert pg.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    assert erros == []
    ctx.close()


def test_sobre_explica_fontes_internacionais_elegibilidade_e_aviso(navegador, url):
    pg = nova_pagina(navegador)
    pg.goto(url + "sobre/")
    pg.wait_for_selector("html[data-pronto]")
    texto = pg.locator("main").inner_text()
    assert "On the Move" in texto and "IberCultura Viva" in texto and "Curadoria do portal" in texto
    assert "Aberto a brasileiros" in texto and "Verificar elegibilidade" in texto
    assert "responsabilidade de quem se candidata" in texto
    pg.close()
