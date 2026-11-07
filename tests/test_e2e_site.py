"""Verificação ponta a ponta do site com Playwright (Chromium headless).

Sobe `python -m http.server` sobre `web/` e usa o snapshot real. Pulado se o
Chromium não estiver instalado (`playwright install chromium`).
"""

import json
import re
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

RAIZ = Path(__file__).resolve().parent.parent
WEB = RAIZ / "web"
SAIDA = RAIZ / "verificacao"
HOSTS_DE_FONTE = ("fonts.googleapis.com", "fonts.gstatic.com")


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def url():
    porta = _porta_livre()
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(porta), "--bind", "127.0.0.1", "-d", str(WEB)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{porta}/"
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", porta), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield base
    proc.terminate()


@pytest.fixture(scope="module")
def navegador():
    with playwright_sync.sync_playwright() as p:
        try:
            nav = p.chromium.launch()
        except Exception as exc:  # navegador não instalado
            pytest.skip(f"Chromium indisponível: {exc}")
        yield nav
        nav.close()


@pytest.fixture
def snapshot():
    return json.loads((WEB / "data" / "snapshot.json").read_text(encoding="utf-8"))


def abertos(snapshot):
    agora = datetime.now(UTC)
    return [e for e in snapshot["editais"] if datetime.fromisoformat(e["inscricao_fim"]) >= agora]


def nova_pagina(navegador, largura=1280, altura=900, permissoes=None):
    ctx = navegador.new_context(
        viewport={"width": largura, "height": altura},
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        accept_downloads=True,
        permissions=permissoes or [],
    )
    pg = ctx.new_page()
    erros: list[str] = []

    def console(m):
        if m.type == "error" and not any(
            h in (m.location.get("url") or "") for h in HOSTS_DE_FONTE
        ):
            erros.append(f"console.error: {m.text}")

    pg.on("console", console)
    pg.on("pageerror", lambda e: erros.append(f"pageerror: {e}"))
    pg.on(
        "requestfailed",
        lambda r: (
            erros.append(f"requestfailed: {r.url}")
            if not any(h in r.url for h in HOSTS_DE_FONTE)
            else None
        ),
    )
    pg.erros = erros
    return pg


def abrir(pg, url, busca=""):
    pg.goto(url + busca)
    pg.wait_for_selector("html[data-pronto]")


def test_renderiza_cards_do_snapshot_sem_erros_no_console(navegador, url, snapshot):
    pg = nova_pagina(navegador)
    abrir(pg, url)
    assert pg.locator(".card").count() == len(abertos(snapshot)) > 0
    assert "cópia offline" in pg.inner_text("#fonte-texto")
    assert re.search(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", pg.inner_text("#fonte-texto"))
    assert pg.erros == []
    assert pg.locator("#radar li").count() == len(snapshot["radar"]) > 0


def test_campo_nulo_aparece_como_nao_informado(navegador, url):
    pg = nova_pagina(navegador)
    abrir(pg, url)
    assert "Não informado no resumo — ver edital" in pg.inner_text("#lista")
    assert "R$ 0,00" not in pg.inner_text("#lista")
    pg.close()


def test_cessao_de_espaco_mostra_pilula_no_lugar_do_raio_x(navegador, url):
    pg = nova_pagina(navegador)
    abrir(pg, url, "?q=Centro+T%C3%A9cnico")
    card = pg.locator(".card", has_text="Centro Técnico de Artes")
    assert card.count() == 1
    assert "Cessão de espaço (sem repasse)" in card.inner_text()
    assert card.locator(".raio-x").count() == 0
    pg.close()


def test_tres_filtros_e_estado_na_url_sobrevive_ao_recarregar(navegador, url):
    pg = nova_pagina(navegador)
    abrir(pg, url)
    pg.get_by_role("button", name="Pessoa física").click()
    pg.get_by_role("button", name="Teatro & Dança").click()
    pg.select_option("#filtro-uf", "CE")
    pg.wait_for_timeout(100)
    antes = pg.locator(".card").count()
    busca = pg.evaluate("location.search")
    assert "perfil=PF" in busca and "uf=CE" in busca and "ling=" in busca
    assert 0 < antes < pg.evaluate("1000")

    pg.goto(url + busca)
    pg.wait_for_selector("html[data-pronto]")
    assert pg.locator(".card").count() == antes
    assert pg.get_by_role("button", name="Pessoa física").get_attribute("aria-pressed") == "true"
    assert pg.get_by_role("button", name="Teatro & Dança").get_attribute("aria-pressed") == "true"
    assert pg.input_value("#filtro-uf") == "CE"
    assert pg.evaluate("location.search") == busca
    pg.close()


def test_uf_inclui_nacionais_e_elegibilidade_desconhecida_continua_visivel(
    navegador, url, snapshot
):
    pg = nova_pagina(navegador)
    abrir(pg, url, "?uf=CE")
    esperados = [e for e in abertos(snapshot) if e["uf"] == "CE" or e["abrangencia"] == "nacional"]
    assert pg.locator(".card").count() == len(esperados)
    abrir(pg, url, "?perfil=COLETIVO")
    sem_info = [e for e in abertos(snapshot) if not e["proponentes"]]
    assert sem_info and pg.locator(
        ".pilula", has_text="Elegibilidade não informada"
    ).count() >= len(sem_info)
    pg.close()


def test_busca_com_debounce_normaliza_acentos(navegador, url):
    pg = nova_pagina(navegador)
    abrir(pg, url)
    total = pg.locator(".card").count()
    pg.fill("#campo-busca", "formacao")
    pg.wait_for_timeout(100)
    assert pg.locator(".card").count() == total  # debounce de 250 ms ainda não disparou
    pg.wait_for_timeout(300)
    n = pg.locator(".card").count()
    assert 0 < n < total
    assert "q=formacao" in pg.evaluate("location.search")
    pg.close()


def test_painel_com_teclado_foco_preso_esc_e_voltar(navegador, url):
    pg = nova_pagina(navegador)
    abrir(pg, url)
    botao = pg.locator(".card h3 button").first
    chave = pg.locator(".card").first.get_attribute("data-chave")
    botao.focus()
    pg.keyboard.press("Enter")
    dialogo = pg.get_by_role("dialog")
    dialogo.wait_for()
    assert dialogo.get_attribute("aria-modal") == "true"
    assert f"edital={chave.replace(':', '%3A')}" in pg.evaluate("location.search")
    for _ in range(15):  # o foco nunca sai do painel
        pg.keyboard.press("Tab")
        assert pg.evaluate("document.activeElement.closest('[role=dialog]') !== null")
    pg.keyboard.press("Shift+Tab")
    assert pg.evaluate("document.activeElement.closest('[role=dialog]') !== null")
    pg.keyboard.press("Escape")
    pg.wait_for_selector("[role=dialog]", state="detached")
    assert pg.evaluate("document.activeElement.textContent") == botao.inner_text()
    assert "edital=" not in pg.evaluate("location.search")

    botao.click()  # reabre e fecha com o botão voltar do navegador
    dialogo.wait_for()
    pg.go_back()
    pg.wait_for_selector("[role=dialog]", state="detached")
    assert pg.erros == []
    pg.close()


def test_link_compartilhado_abre_o_mesmo_painel(navegador, url):
    pg = nova_pagina(navegador)
    abrir(pg, url, "?ling=Literatura")
    pg.locator(".card h3 button").first.click()
    titulo = pg.locator("#painel-titulo").inner_text()
    link = pg.evaluate("location.href")
    outra = nova_pagina(navegador)
    outra.goto(link)
    outra.wait_for_selector("[role=dialog]")
    assert outra.locator("#painel-titulo").inner_text() == titulo
    assert outra.locator("#painel-titulo").inner_text() != ""
    # O fundo fica aria-hidden com o painel aberto; por isso o seletor não usa role.
    assert (
        outra.locator("#chips-linguagem button", has_text="Literatura").get_attribute(
            "aria-pressed"
        )
        == "true"
    )
    pg.close()
    outra.close()


def test_painel_mostra_proveniencia_links_e_fonte(navegador, url):
    pg = nova_pagina(navegador)
    abrir(pg, url, "?q=Funarte+Aberta+Livraria")
    pg.locator(".card h3 button").first.click()
    painel = pg.get_by_role("dialog")
    texto = painel.inner_text()
    assert "Fonte: Curadoria" in texto and "coletado em" in texto
    assert "documentos exigidos" in texto.lower() and "Anexo I" in texto
    assert (
        painel.get_by_role("link", name="Página oficial")
        .get_attribute("href")
        .startswith("https://")
    )
    assert painel.locator("details summary").inner_text() == "Como sabemos disso"
    pg.close()


def test_favoritar_persiste_e_exporta(navegador, url, tmp_path):
    pg = nova_pagina(navegador)
    abrir(pg, url)
    fav = pg.locator(".card .btn-fav").first
    chave = fav.get_attribute("data-favorito")
    fav.click()
    assert fav.get_attribute("aria-pressed") == "true"
    assert pg.inner_text("#contador-favoritos") == "1"
    pg.reload()
    pg.wait_for_selector("html[data-pronto]")
    assert pg.locator(f'[data-favorito="{chave}"]').get_attribute("aria-pressed") == "true"
    assert pg.inner_text("#contador-favoritos") == "1"

    pg.get_by_role("tab", name=re.compile("Meus favoritos")).click()
    assert pg.locator(".card").count() == 1
    with pg.expect_download() as d:
        pg.get_by_role("button", name="Exportar CSV").click()
    csv = Path(d.value.path()).read_bytes()
    assert csv.startswith(b"\xef\xbb\xbf") and b"\r\n" in csv and b";" in csv
    with pg.expect_download() as d:
        pg.get_by_role("button", name=re.compile(r"\.ics")).click()
    ics = Path(d.value.path()).read_bytes().decode("utf-8")  # bytes: preserva o CRLF
    assert (
        ics.startswith("BEGIN:VCALENDAR")
        and "BEGIN:VEVENT" in ics
        and ics.count("BEGIN:VEVENT") == 1
    )
    assert all(len(linha.encode()) <= 75 for linha in ics.split("\r\n"))
    assert "Fim da inscrição" in ics.replace("\r\n ", "")
    pg.get_by_role("button", name="Copiar lista para WhatsApp").click()
    pg.wait_for_function("document.querySelector('#aviso-live').textContent.startsWith('Lista')")
    assert pg.erros == []
    pg.close()


def test_favorito_removido_da_base_continua_listado(navegador, url):
    pg = nova_pagina(navegador)
    abrir(pg, url)
    pg.add_init_script(
        """localStorage.setItem('editais-culturais:favoritos:v1', JSON.stringify({
        'curadoria:edital-que-saiu': {chave:'curadoria:edital-que-saiu', titulo:'Edital Teste Removido',
        orgao_fomento:'Órgão Fictício', url_origem:'https://exemplo.invalid/x',
        inscricao_fim:'2026-01-10T02:59:59+00:00', fim_tem_horario:false}}))"""
    )
    pg.reload()
    pg.wait_for_selector("html[data-pronto]")
    pg.get_by_role("tab", name=re.compile("Meus favoritos")).click()
    assert "Encerrado ou removido" in pg.inner_text("#lista")
    pg.close()


def test_copiar_whatsapp_com_clipboard_e_com_fallback(navegador, url):
    pg = nova_pagina(navegador, permissoes=["clipboard-read", "clipboard-write"])
    abrir(pg, url, "?q=Funarte+Aberta+Livraria")
    pg.locator(".card h3 button").first.click()
    pg.get_by_role("button", name="Copiar resumo para WhatsApp").click()
    pg.wait_for_function("document.querySelector('#aviso-live').textContent.includes('copiado')")
    copiado = pg.evaluate("navigator.clipboard.readText()")
    assert copiado.startswith("*Programa Funarte Aberta 2026")
    assert "Prazo: até" in copiado and "Quem pode participar:" in copiado
    assert re.search(r"\?edital=curadoria%3Afunarte", copiado)
    assert "Cessão de espaço (sem repasse)" in copiado
    assert pg.erros == []
    pg.close()

    sem = nova_pagina(navegador)
    sem.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: undefined})")
    abrir(sem, url, "?q=Funarte+Aberta+Livraria")
    sem.locator(".card h3 button").first.click()
    sem.get_by_role("button", name="Copiar resumo para WhatsApp").click()
    sem.wait_for_function("document.querySelector('#aviso-live').textContent.length > 0")
    assert sem.erros == []
    sem.close()


def test_localstorage_indisponivel_avisa_e_funciona_em_memoria(navegador, url):
    pg = nova_pagina(navegador)
    pg.add_init_script(
        "Object.defineProperty(window, 'localStorage', {get(){ throw new Error('bloqueado') }})"
    )
    abrir(pg, url)
    pg.locator(".card .btn-fav").first.click()
    assert pg.inner_text("#contador-favoritos") == "1"
    assert "Não foi possível salvar" in pg.inner_text("#aviso-live")
    pg.close()


def test_modo_ao_vivo_usa_supabase_e_busca_textual(navegador, url):
    """Simula o supabase-js (interceptando o esm.sh) para exercitar o caminho ao vivo."""
    modulo = """
    export function createClient() {
      const consulta = (tabela) => {
        const q = {
          select: () => q, order: () => q,
          textSearch: (coluna, termo, opcoes) => {
            (window.__buscas ||= []).push({ tabela, coluna, termo, opcoes });
            return Promise.resolve({ data: [], error: null });
          },
          then: (ok, falha) => fetch('data/snapshot.json').then((r) => r.json()).then((s) =>
            ({ data: tabela === 'radar_editais' ? s.radar : s.editais, error: null })).then(ok, falha),
        };
        return q;
      };
      return { from: consulta };
    }"""
    pg = nova_pagina(navegador)
    pg.route(
        "**/config.js",
        lambda r: r.fulfill(
            content_type="text/javascript",
            body='window.EDITAIS_CONFIG={supabaseUrl:"https://projeto.supabase.invalid",supabaseAnonKey:"anon-de-teste"};',
        ),
    )
    pg.route("https://esm.sh/**", lambda r: r.fulfill(content_type="text/javascript", body=modulo))
    abrir(pg, url)
    assert pg.inner_text("#fonte-texto") == "Dados ao vivo"
    assert pg.locator(".card").count() > 0
    pg.fill("#campo-busca", "musica")
    pg.wait_for_timeout(500)
    busca = pg.evaluate("window.__buscas")
    assert busca[-1]["coluna"] == "busca_textual"
    assert busca[-1]["opcoes"] == {"config": "portuguese", "type": "websearch"}
    assert pg.erros == []
    pg.close()


def test_supabase_fora_do_ar_cai_no_snapshot(navegador, url):
    pg = nova_pagina(navegador)
    pg.route(
        "**/config.js",
        lambda r: r.fulfill(
            content_type="text/javascript",
            body='window.EDITAIS_CONFIG={supabaseUrl:"https://projeto.supabase.invalid",supabaseAnonKey:"anon"};',
        ),
    )
    pg.route("https://esm.sh/**", lambda r: r.abort())
    pg.goto(url)
    pg.wait_for_selector("html[data-pronto]")
    assert "cópia offline" in pg.inner_text("#fonte-texto")
    assert pg.locator(".card").count() > 0
    assert "cópia offline" in pg.inner_text("#aviso-topo")
    pg.close()


def test_estado_vazio_sugere_afrouxar_filtros(navegador, url):
    pg = nova_pagina(navegador)
    abrir(pg, url, "?q=zzzzinexistente")
    assert "Nenhum edital com esses filtros" in pg.inner_text("#lista")
    pg.get_by_role("button", name="Limpar filtros").click()
    assert pg.locator(".card").count() > 0
    pg.close()


@pytest.mark.parametrize(
    ("largura", "altura", "nome"), [(375, 800, "mobile-375"), (1280, 900, "desktop-1280")]
)
def test_sem_rolagem_horizontal_e_screenshots(navegador, url, largura, altura, nome):
    SAIDA.mkdir(exist_ok=True)
    pg = nova_pagina(navegador, largura, altura)
    abrir(pg, url)
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    pg.screenshot(path=str(SAIDA / f"{nome}.png"), full_page=True)
    pg.locator(".card h3 button").first.click()
    pg.get_by_role("dialog").wait_for()
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    pg.wait_for_timeout(300)  # termina a animação de entrada do painel
    pg.screenshot(path=str(SAIDA / f"{nome}-painel.png"))
    pg.close()
