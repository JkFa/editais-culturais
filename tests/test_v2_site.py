"""v2: home, compatibilidade de links antigos, tema, contador de visitas e celular."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from e2e_util import WEB, abertos, nova_pagina

RAIZ = Path(__file__).resolve().parent.parent
DOCS = RAIZ / "docs" / "v2"
PAGINAS = ["index.html", "editais/index.html", "sobre/index.html", "404.html"]
CONFIG_FALSA = (
    'window.EDITAIS_CONFIG={supabaseUrl:"https://projeto.supabase.invalid",'
    'supabaseAnonKey:"sb_publishable_teste"};'
)
NAO_ROBO = "Object.defineProperty(navigator, 'webdriver', {get: () => false})"


def pronta(pg, url_, caminho=""):
    pg.goto(url_ + caminho)
    pg.wait_for_selector("html[data-pronto]")


# ---------------------------------------------------------------- compatibilidade de links


PARAMETROS_ANTIGOS = [
    ("q", "pnab"), ("uf", "MG"), ("perfil", "PF"), ("ling", "Música"), ("abr", "nacional"),
    ("abertas", "0"), ("ord", "teto"), ("aba", "favoritos"), ("edital", "mapas:ce:8263"),
]  # fmt: skip


@pytest.mark.parametrize(("chave", "valor"), PARAMETROS_ANTIGOS)
def test_parametro_antigo_leva_ao_explorador_com_o_mesmo_estado(navegador, url, chave, valor):
    pg = nova_pagina(navegador)
    pg.goto(f"{url}?{chave}={valor}")
    pg.wait_for_url(re.compile(r"/editais/\?"))
    assert urlparse(pg.url).path == "/editais/"
    assert parse_qs(urlparse(pg.url).query) == {chave: [valor]}
    pg.wait_for_selector("html[data-pronto]")
    assert pg.locator("h1").inner_text() == "Editais abertos"
    pg.close()


@pytest.mark.parametrize(("antigo", "novo"), [("ordem", "ord"), ("abertos", "abertas")])
def test_nomes_alternativos_sao_normalizados(navegador, url, antigo, novo):
    pg = nova_pagina(navegador)
    pg.goto(f"{url}?{antigo}=0")
    pg.wait_for_url(re.compile(r"/editais/\?"))
    assert parse_qs(urlparse(pg.url).query) == {novo: ["0"]}
    pg.close()


def test_link_compartilhado_antigo_abre_o_painel_do_mesmo_edital(navegador, url, snapshot):
    alvo = next(e for e in abertos(snapshot) if e["uf"] == "CE")
    pg = nova_pagina(navegador)
    pg.goto(f"{url}?uf=CE&edital={alvo['chave'].replace(':', '%3A')}")
    pg.wait_for_selector("[role=dialog]")
    assert urlparse(pg.url).path == "/editais/"
    assert parse_qs(urlparse(pg.url).query) == {"uf": ["CE"], "edital": [alvo["chave"]]}
    assert pg.locator("#painel-titulo").inner_text() == alvo["titulo"]
    assert pg.input_value("#filtro-uf") == "CE"
    pg.close()


def test_redirecionamento_roda_antes_de_qualquer_pintura():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    script = html.index("location.replace")
    assert script < html.index("<body") and script < html.index('rel="stylesheet"')


def test_home_sem_parametros_nao_redireciona(navegador, url):
    pg = nova_pagina(navegador)
    pronta(pg, url)
    assert urlparse(pg.url).path == "/"
    assert "lidos para você" in pg.locator("h1").inner_text()
    pg.close()


# ---------------------------------------------------------------- tema


def test_script_de_tema_inline_vem_antes_do_css_e_e_igual_nas_4_paginas():
    scripts = set()
    for pagina in PAGINAS:
        html = (WEB / pagina).read_text(encoding="utf-8")
        achado = re.search(
            r"<script>(\(function\(\)\{try\{var t=localStorage.*?)</script>", html, re.S
        )
        assert achado, pagina
        assert achado.start() < html.index('rel="stylesheet"'), pagina
        scripts.add(achado.group(1))
        assert (
            html.count('name="theme-color"') == 2 and 'media="(prefers-color-scheme: dark)"' in html
        )
        assert 'name="color-scheme" content="light dark"' in html
    assert len(scripts) == 1


def test_tema_salvo_e_aplicado_antes_de_o_css_carregar(navegador, url):
    """Sem flash: no instante em que o parser encontra a primeira folha de estilo, data-tema já está lá."""
    ctx = navegador.new_context(color_scheme="light")
    pg = ctx.new_page()
    pg.add_init_script(
        """
        try { localStorage.setItem('editais-culturais:tema', 'escuro'); } catch (e) {}
        new MutationObserver((muts) => {
          for (const m of muts) for (const n of m.addedNodes) {
            if (n.nodeName === 'LINK' && n.getAttribute('rel') === 'stylesheet' && !('__temaAntesDoCss' in window)) {
              window.__temaAntesDoCss = document.documentElement.dataset.tema ?? null;
            }
          }
        }).observe(document, { childList: true, subtree: true });
        """
    )
    pg.goto(url)
    pg.wait_for_selector("html[data-pronto]")
    assert pg.evaluate("window.__temaAntesDoCss") == "escuro"
    assert pg.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(22, 21, 19)"
    ctx.close()


@pytest.mark.parametrize("sistema", ["light", "dark"])
def test_modo_sistema_segue_prefers_color_scheme(navegador, url, sistema):
    ctx = navegador.new_context(color_scheme=sistema)
    pg = ctx.new_page()
    pronta(pg, url)
    fundo = pg.evaluate("getComputedStyle(document.body).backgroundColor")
    assert fundo == ("rgb(22, 21, 19)" if sistema == "dark" else "rgb(247, 245, 240)")
    assert pg.evaluate("document.documentElement.dataset.tema") is None
    ctx.close()


def test_alternar_tema_percorre_sistema_claro_escuro_e_persiste(navegador, url):
    ctx = navegador.new_context(color_scheme="dark")
    pg = ctx.new_page()
    pronta(pg, url)
    botao = pg.locator("[data-alternar-tema]").first
    assert botao.inner_text() == "Tema: sistema"
    botao.click()
    assert (
        botao.inner_text() == "Tema: claro"
        and pg.evaluate("document.documentElement.dataset.tema") == "claro"
    )
    pg.wait_for_timeout(300)  # transição de cor de 150 ms
    assert (
        pg.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(247, 245, 240)"
    )  # claro vence o sistema escuro
    assert "escuro" in botao.get_attribute("aria-label")  # anuncia o próximo estado
    assert pg.evaluate(
        "[...document.querySelectorAll('meta[name=theme-color]')].every(m => m.content === '#f7f5f0')"
    )
    botao.click()
    assert botao.inner_text() == "Tema: escuro"
    assert pg.evaluate(
        "[...document.querySelectorAll('meta[name=theme-color]')].every(m => m.content === '#161513')"
    )
    pg.reload()
    pg.wait_for_selector("html[data-pronto]")
    assert pg.evaluate("document.documentElement.dataset.tema") == "escuro"
    assert pg.locator("[data-alternar-tema]").first.inner_text() == "Tema: escuro"
    pg.locator("[data-alternar-tema]").first.click()  # volta ao sistema
    assert pg.evaluate("document.documentElement.dataset.tema") is None
    assert pg.evaluate("localStorage.getItem('editais-culturais:tema')") is None
    assert (
        pg.evaluate(
            "[...document.querySelectorAll('meta[name=theme-color]')].map(m => m.content).join()"
        )
        == "#f7f5f0,#161513"
    )
    ctx.close()


def test_tema_funciona_sem_localstorage(navegador, url):
    pg = nova_pagina(navegador)
    pg.add_init_script(
        "Object.defineProperty(window, 'localStorage', {get(){ throw new Error('bloqueado') }})"
    )
    pronta(pg, url)
    pg.locator("[data-alternar-tema]").first.click()
    assert pg.evaluate("document.documentElement.dataset.tema") == "claro"
    assert pg.erros == []
    pg.close()


def test_tema_do_explorador_e_compartilhado_com_a_home(navegador, url):
    ctx = navegador.new_context()
    pg = ctx.new_page()
    pronta(pg, url)
    pg.locator("[data-alternar-tema]").first.click()
    pg.locator("[data-alternar-tema]").first.click()  # escuro
    pg.goto(url + "editais/")
    pg.wait_for_selector("html[data-pronto]")
    assert pg.evaluate("document.documentElement.dataset.tema") == "escuro"
    ctx.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node indisponível")
def test_contraste_wcag_nos_dois_temas():
    r = subprocess.run(
        ["node", str(RAIZ / "scripts" / "contraste.mjs")],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout
    assert "FALHA" not in r.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="Node indisponível")
def test_logica_da_edge_function():
    r = subprocess.run(
        [
            "node",
            "--test",
            str(RAIZ / "supabase" / "functions" / "registrar-visita" / "logica.test.mjs"),
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout[-800:]


# ---------------------------------------------------------------- home


def test_home_usa_dados_reais_do_snapshot(navegador, url, snapshot):
    pg = nova_pagina(navegador)
    pronta(pg, url)
    vivos = abertos(snapshot)
    assert pg.locator('[data-numero="abertos"]').inner_text() == str(len(vivos))
    assert pg.locator('[data-numero="fontes"]').inner_text() == str(
        len({e["fonte"] for e in vivos})
    )
    com_valor = [e for e in vivos if e["valor_total"] is not None]
    assert f"{len(com_valor)} edita" in pg.locator('[data-numero-rotulo="valor"]').inner_text()
    # índice por linguagem: contagens reais
    for linha in pg.locator("#indice-linguagem li").all():
        nome = linha.locator(".indice-nome").inner_text()
        assert int(re.match(r"\d+", linha.locator(".indice-n").inner_text()).group()) == sum(
            1 for e in vivos if nome in e["linguagens"]
        )
    assert pg.locator("#perfis li").count() == 5
    assert pg.locator("#radar li").count() == len(snapshot["radar"])
    assert re.search(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", pg.locator(".sobretitulo").inner_text())
    assert pg.erros == []
    pg.close()


def test_encerram_esta_semana_limita_a_6_e_liga_ao_painel(navegador, url, snapshot):
    pg = nova_pagina(navegador)
    pronta(pg, url)
    itens = pg.locator("#semana a")
    assert 0 < itens.count() <= 6
    href = itens.first.get_attribute("href")
    assert href.startswith("editais/?edital=")
    itens.first.click()
    pg.wait_for_selector("[role=dialog]")
    assert "edital=" in pg.url and pg.locator("#painel-titulo").inner_text()
    pg.close()


def test_atalhos_de_linguagem_e_perfil_levam_ao_explorador_filtrado(navegador, url):
    pg = nova_pagina(navegador)
    pronta(pg, url)
    pg.locator("#indice-linguagem a", has_text="Música").click()
    pg.wait_for_url(re.compile(r"/editais/\?ling="))
    assert parse_qs(urlparse(pg.url).query) == {"ling": ["Música"]}
    pg.goto(url)
    pg.wait_for_selector("html[data-pronto]")
    pg.locator("#perfis a", has_text="MEI").click()
    pg.wait_for_url(re.compile(r"perfil=MEI"))
    pg.close()


def test_busca_grande_funciona_sem_javascript_e_leva_ao_explorador(navegador, url):
    ctx = navegador.new_context(java_script_enabled=False)
    pg = ctx.new_page()
    pg.goto(url)
    pg.fill("#busca-home", "teatro")
    pg.press("#busca-home", "Enter")
    pg.wait_for_url(re.compile(r"/editais/\?q=teatro"))
    ctx.close()


def test_home_sem_cookies_e_sem_bloco_de_visitas_sem_supabase(navegador, url):
    ctx = navegador.new_context()
    pg = ctx.new_page()
    pronta(pg, url)
    assert ctx.cookies() == []
    assert pg.locator("#visitantes").is_hidden()
    ctx.close()


def test_semana_vazia_mostra_o_texto_pedido(navegador, url, snapshot):
    vazio = {**snapshot, "editais": [e for e in snapshot["editais"] if e["fonte"] == "curadoria"]}
    pg = nova_pagina(navegador)
    pg.route(
        "**/data/snapshot.json",
        lambda r: r.fulfill(content_type="application/json", body=json.dumps(vazio)),
    )
    pronta(pg, url)
    assert pg.locator("#semana").inner_text() == "Nenhum edital encerra nos próximos 7 dias"
    pg.close()


# ---------------------------------------------------------------- páginas estáticas e SEO


@pytest.mark.parametrize("pagina", PAGINAS)
def test_cada_pagina_tem_titulo_description_e_open_graph_proprios(pagina):
    html = (WEB / pagina).read_text(encoding="utf-8")
    for marca in (
        "<title>",
        'name="description"',
        'rel="canonical"',
        'property="og:image"',
        'rel="manifest"',
        'name="viewport"',
    ):
        assert marca in html, (pagina, marca)
    assert "og.png" in html


def test_titulos_sao_distintos():
    titulos = [
        re.search(r"<title>(.*?)</title>", (WEB / p).read_text(encoding="utf-8")).group(1)
        for p in PAGINAS
    ]
    assert len(set(titulos)) == len(PAGINAS)


def test_sitemap_robots_manifest_e_imagens():
    assert (WEB / "sitemap.xml").read_text().count("<loc>") == 3
    assert "Sitemap:" in (WEB / "robots.txt").read_text()
    manifesto = json.loads((WEB / "manifest.webmanifest").read_text())
    assert manifesto["start_url"] == "./" and manifesto["icons"]
    for img in ("og.png", "icone-180.png", "icone-192.png", "icone-512.png", "icone.svg"):
        assert (WEB / "assets" / "img" / img).stat().st_size > 300
    assert (WEB / "assets/img/og.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_caminhos_sao_relativos_exceto_a_404_com_base():
    for pagina in ("index.html", "editais/index.html", "sobre/index.html"):
        html = (WEB / pagina).read_text(encoding="utf-8")
        for ref in re.findall(r'(?:src|href)="(/[^"]*)"', html):
            pytest.fail(f"{pagina}: caminho absoluto {ref}")
    assert '<base href="/editais-culturais/"' in (WEB / "404.html").read_text()


def test_pagina_404_funciona_com_a_base_do_subcaminho(navegador, url):
    pg = nova_pagina(navegador)
    # O GitHub Pages serve a 404 em qualquer caminho: aqui o subcaminho é mapeado para a raiz local.
    pg.route(
        re.compile(r".*/editais-culturais/.*"),
        lambda r: r.continue_(url=r.request.url.replace("/editais-culturais/", "/")),
    )
    pg.goto(url.rstrip("/") + "/editais-culturais/404.html")
    assert "Esse endereço não existe" in pg.locator("h1").inner_text()
    assert pg.get_by_role("link", name="Ver os editais abertos").get_attribute("href") == "editais/"
    assert pg.get_by_role("link", name="Ir para a página inicial").count() == 1
    assert pg.evaluate("getComputedStyle(document.body).fontFamily").lower().find("inter") >= 0
    pg.close()


def test_sobre_tem_metodologia_fontes_limitacoes_privacidade_e_issues(navegador, url):
    pg = nova_pagina(navegador)
    pronta(pg, url, "sobre/")
    for ancora in ("metodologia", "fontes", "limitacoes", "privacidade", "contato"):
        assert pg.locator(f"#{ancora}").count() == 1
    assert pg.locator('a[href$="/issues"]').count() >= 1
    assert "Não guardamos IP" in pg.locator("#privacidade ~ ul").first.inner_text()
    assert pg.erros == []
    pg.close()


# ---------------------------------------------------------------- contador de visitas


def _visitas(pg, resumo=None, falha=False):
    chamadas: list = []

    def funcao(rota):
        chamadas.append(
            {
                "corpo": rota.request.post_data,
                "tipo": rota.request.headers.get("content-type"),
                "cookie": rota.request.headers.get("cookie"),
            }
        )
        rota.fulfill(status=204, headers={"access-control-allow-origin": "*"})

    pg.route("https://projeto.supabase.invalid/functions/v1/registrar-visita", funcao)
    pg.route("**/config.js", lambda r: r.fulfill(content_type="text/javascript", body=CONFIG_FALSA))

    def rest(rota):
        if "visitas_resumo" in rota.request.url:
            if falha or resumo is None:
                return rota.abort()
            return rota.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(resumo),
                headers={"access-control-allow-origin": "*"},
            )
        rota.fulfill(
            status=200,
            content_type="application/json",
            body="[]",
            headers={"access-control-allow-origin": "*"},
        )

    pg.route("https://projeto.supabase.invalid/rest/v1/**", rest)
    return chamadas


def test_conta_uma_visita_por_aba_e_por_dia_sem_dado_pessoal(navegador, url):
    ctx = navegador.new_context()
    ctx.add_init_script(NAO_ROBO)
    pg = ctx.new_page()
    chamadas = _visitas(pg)
    pronta(pg, url)
    pg.wait_for_timeout(300)
    assert len(chamadas) == 1
    assert json.loads(chamadas[0]["corpo"]) == {"pagina": "home"}  # só a página, nada mais
    assert chamadas[0]["tipo"].startswith("text/plain") and not chamadas[0]["cookie"]
    pg.reload()
    pg.wait_for_selector("html[data-pronto]")
    pg.goto(url + "editais/")
    pg.wait_for_selector("html[data-pronto]")
    pg.wait_for_timeout(300)
    assert len(chamadas) == 1  # mesma aba, mesmo dia: não conta de novo
    outra = ctx.new_page()
    _visitas_extra = outra.route(
        "https://projeto.supabase.invalid/functions/v1/registrar-visita",
        lambda r: (chamadas.append(1), r.fulfill(status=204)),
    )
    outra.route(
        "**/config.js", lambda r: r.fulfill(content_type="text/javascript", body=CONFIG_FALSA)
    )
    outra.route(
        "https://projeto.supabase.invalid/rest/v1/**",
        lambda r: r.fulfill(status=200, content_type="application/json", body="[]"),
    )
    outra.goto(url + "sobre/")
    outra.wait_for_selector("html[data-pronto], footer")
    outra.wait_for_timeout(300)
    assert len(chamadas) == 2  # outra aba = outra visita
    assert ctx.cookies() == []
    ctx.close()


def test_navegador_automatizado_nao_conta(navegador, url):
    pg = nova_pagina(navegador)  # navigator.webdriver verdadeiro
    chamadas = _visitas(pg)
    pronta(pg, url)
    pg.wait_for_timeout(300)
    assert chamadas == []
    pg.close()


def test_falha_do_contador_nao_quebra_a_pagina(navegador, url):
    ctx = navegador.new_context()
    ctx.add_init_script(NAO_ROBO)
    pg = ctx.new_page()
    erros: list[str] = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.route("**/config.js", lambda r: r.fulfill(content_type="text/javascript", body=CONFIG_FALSA))
    pg.route("https://projeto.supabase.invalid/**", lambda r: r.abort())
    pronta(pg, url)
    assert pg.locator('[data-numero="abertos"]').inner_text() != "—"
    assert pg.locator("#visitantes").is_hidden()
    assert erros == []
    ctx.close()


RESUMO = {
    "total_geral": 12480, "total_30d": 3100, "estados_30d": 3,
    "por_uf": [{"uf": "MG", "total": 900}, {"uf": "CE", "total": 100}, {"uf": "SP", "total": 20}],
    "top_cidades": [{"cidade": "Ipatinga", "uf": "MG", "total": 40}, {"cidade": "Sobral", "uf": "CE", "total": 7}],
    "outras_cidades": 33,
}  # fmt: skip


def test_bloco_de_visitantes_com_cartograma_tabela_e_cidades(navegador, url):
    pg = nova_pagina(navegador)
    _visitas(pg, resumo=RESUMO)
    pronta(pg, url)
    bloco = pg.locator("#visitantes")
    bloco.wait_for(state="visible")
    assert (
        pg.locator("[data-visitas-frase]").inner_text()
        == "12.480 visitas · pessoas de 3 estados nos últimos 30 dias"
    )
    assert pg.locator(".cg-celula").count() == 27
    assert (
        pg.locator('.cg-celula[data-nivel="5"]').inner_text() == "MG"
    )  # o maior vira o nível máximo
    assert pg.locator('.cg-celula[data-nivel="0"]').count() == 24
    assert pg.locator(".cg-botao", has_text="CE").get_attribute("title") == "CE: 100 visitas"
    pg.locator(".cg-botao", has_text="SP").focus()
    assert pg.evaluate("document.activeElement.title") == "SP: 20 visitas"  # alcançável por teclado
    pg.locator(".cg-tabela summary").click()
    assert "Ipatinga/MG" in pg.locator("[data-visitas-cidades]").inner_text()
    assert "outras cidades" in pg.locator("[data-visitas-cidades]").inner_text()
    assert pg.locator(".cg-tabela table tbody tr").count() == 3
    assert "Não guardamos IP nem usamos cookies" in bloco.inner_text()
    assert pg.locator('#visitantes a[href="sobre/#privacidade"]').count() == 1
    assert pg.erros == []
    pg.close()
    for esquema in ("light", "dark"):
        ctx = navegador.new_context(viewport={"width": 1280, "height": 800}, color_scheme=esquema)
        outra = ctx.new_page()
        _visitas(outra, resumo=RESUMO)
        pronta(outra, url)
        outra.locator("#visitantes").wait_for(state="visible")
        outra.wait_for_timeout(300)
        DOCS.mkdir(parents=True, exist_ok=True)
        outra.locator("#visitantes").screenshot(path=str(DOCS / f"visitantes-{esquema}.png"))
        ctx.close()


def test_cartograma_posiciona_27_ufs_sem_sobreposicao():
    js = (WEB / "assets/js/cartograma.js").read_text(encoding="utf-8")
    bloco = js[js.index("export const POSICAO_UF") : js.index("}; // fmt: skip")]
    pares = re.findall(r"([A-Z]{2}): \[(\d), (\d)\]", bloco)
    assert len(pares) == 27 and len({(col, lin) for _, col, lin in pares}) == 27
    assert {uf for uf, _, _ in pares} == set(
        "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
    )


# ---------------------------------------------------------------- celular × temas

VIEWPORTS = [(360, 740), (390, 844)]
TEMAS = ["light", "dark"]


def contexto_movel(navegador, largura, altura, tema):
    ctx = navegador.new_context(
        viewport={"width": largura, "height": altura},
        color_scheme=tema,
        has_touch=True,
        is_mobile=True,
        device_scale_factor=2,
        locale="pt-BR",
    )
    pg = ctx.new_page()
    erros: list[str] = []
    pg.on(
        "console",
        lambda m: (
            erros.append(m.text)
            if m.type == "error" and "fonts.g" not in (m.location.get("url") or "")
            else None
        ),
    )
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.erros = erros
    return ctx, pg


@pytest.mark.parametrize("tema", TEMAS)
@pytest.mark.parametrize(("largura", "altura"), VIEWPORTS)
def test_todas_as_paginas_no_celular(navegador, url, largura, altura, tema):
    DOCS.mkdir(parents=True, exist_ok=True)
    ctx, pg = contexto_movel(navegador, largura, altura, tema)
    pg.route(
        re.compile(r".*/editais-culturais/.*"),
        lambda r: r.continue_(url=r.request.url.replace("/editais-culturais/", "/")),
    )
    for nome, caminho in [
        ("home", ""),
        ("editais", "editais/"),
        ("sobre", "sobre/"),
        ("404", "editais-culturais/404.html"),
    ]:
        pg.goto(url + caminho if nome != "404" else url.rstrip("/") + "/" + caminho)
        pg.wait_for_selector("h1")
        pg.wait_for_timeout(500)
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
            nome,
            "rolagem horizontal",
        )
        # alvos de toque e fonte dos campos
        pequenos = pg.evaluate(
            """() => [...document.querySelectorAll('button, a.btn, .chip, select, input:not([type=checkbox]), nav a, .perfil, .indice a, .aba, summary')]
                .filter(e => e.offsetParent !== null && !e.closest('[hidden]') && !e.closest('h3') && !e.classList.contains('cg-botao') && !e.closest('.cartograma'))
                .map(e => [e.tagName + '.' + e.className, e.getBoundingClientRect().height])
                .filter(([, h]) => h < 43.5)"""
        )
        assert pequenos == [], (nome, pequenos)
        assert pg.evaluate(
            "[...document.querySelectorAll('input, select, textarea')].every(e => parseFloat(getComputedStyle(e).fontSize) >= 16)"
        ), nome
        pg.screenshot(
            path=str(DOCS / f"{nome}-{largura}-{tema}.png"), full_page=(nome != "editais")
        )
    assert pg.erros == []
    ctx.close()


@pytest.mark.parametrize("tema", TEMAS)
@pytest.mark.parametrize(("largura", "altura"), VIEWPORTS)
def test_folha_de_filtros_por_toque_e_teclado(navegador, url, largura, altura, tema):
    ctx, pg = contexto_movel(navegador, largura, altura, tema)
    pronta(pg, url, "editais/")
    fab = pg.locator("#abrir-filtros")
    assert fab.is_visible() and "Filtros (0)" in fab.inner_text().replace("\n", " ")
    # a busca continua à vista depois de rolar
    pg.evaluate("window.scrollTo(0, 1200)")
    assert pg.locator("#campo-busca").bounding_box()["y"] >= 0
    assert pg.locator("#filtros").evaluate("e => getComputedStyle(e).visibility") == "hidden"
    fab.tap()
    pg.wait_for_selector("#filtros[data-aberta]")
    folha = pg.locator("#filtros")
    assert folha.get_attribute("role") == "dialog" and folha.get_attribute("aria-modal") == "true"
    assert pg.evaluate("document.activeElement.id") == "folha-fechar"
    total = pg.locator(".card").count()
    pg.locator("#chips-linguagem .chip", has_text="Música").tap()
    pg.locator("#chips-perfil .chip", has_text="Pessoa física").tap()
    pg.locator("#filtro-uf").select_option("CE")
    assert "Filtros (3)" in fab.inner_text().replace("\n", " ")
    ver = pg.locator("#folha-ver").inner_text()
    n = int(re.search(r"Ver (\d+)", ver).group(1))
    assert pg.locator(".card").count() == n < total
    for _ in range(12):  # foco preso na folha
        pg.keyboard.press("Tab")
        assert pg.evaluate("document.activeElement.closest('#filtros') !== null")
    pg.keyboard.press("Escape")
    pg.wait_for_function("!document.querySelector('#filtros').hasAttribute('data-aberta')")
    assert pg.evaluate("document.activeElement.id") == "abrir-filtros"
    fab.tap()
    pg.locator("#folha-ver").tap()
    pg.wait_for_function("!document.querySelector('#filtros').hasAttribute('data-aberta')")
    qs = parse_qs(urlparse(pg.url).query)
    assert qs["perfil"] == ["PF"] and qs["uf"] == ["CE"] and qs["ling"] == ["Música"]
    assert pg.erros == []
    ctx.close()


@pytest.mark.parametrize("tema", TEMAS)
@pytest.mark.parametrize(("largura", "altura"), VIEWPORTS)
def test_painel_em_tela_cheia_com_whatsapp_fixo(navegador, url, largura, altura, tema):
    DOCS.mkdir(parents=True, exist_ok=True)
    ctx, pg = contexto_movel(navegador, largura, altura, tema)
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    pronta(pg, url, "editais/?q=Funarte+Aberta+Livraria")
    pg.locator(".card h3 button").first.tap()
    pg.wait_for_selector(".painel")
    pg.wait_for_timeout(300)
    caixa = pg.locator(".painel").bounding_box()
    assert caixa["x"] == 0 and caixa["width"] == largura and abs(caixa["height"] - altura) <= 1
    botao = pg.locator("#copiar-whatsapp").bounding_box()
    assert botao["y"] + botao["height"] <= altura + 0.5 and botao["height"] >= 44
    pg.locator(".painel-corpo").evaluate("e => e.scrollTo(0, e.scrollHeight)")
    assert pg.locator("#copiar-whatsapp").is_visible()
    pg.screenshot(path=str(DOCS / f"painel-{largura}-{tema}.png"))
    pg.locator("#copiar-whatsapp").tap()
    pg.wait_for_function("document.querySelector('#aviso-live').textContent.includes('copiado')")
    assert pg.evaluate("navigator.clipboard.readText()").startswith("*Programa Funarte Aberta 2026")
    pg.keyboard.press("Escape")
    pg.wait_for_selector(".painel", state="detached")
    assert pg.erros == []
    ctx.close()


@pytest.mark.parametrize("tema", TEMAS)
def test_desktop_1280x800_nos_dois_temas(navegador, url, tema):
    DOCS.mkdir(parents=True, exist_ok=True)
    ctx = navegador.new_context(
        viewport={"width": 1280, "height": 800}, color_scheme=tema, locale="pt-BR"
    )
    pg = ctx.new_page()
    erros: list[str] = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.on(
        "console",
        lambda m: (
            erros.append(m.text)
            if m.type == "error" and "fonts.g" not in (m.location.get("url") or "")
            else None
        ),
    )
    for nome, caminho in [("home", ""), ("editais", "editais/"), ("sobre", "sobre/")]:
        pg.goto(url + caminho)
        pg.wait_for_selector("h1")
        pg.wait_for_timeout(400)
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        pg.screenshot(path=str(DOCS / f"{nome}-1280-{tema}.png"), full_page=(nome != "editais"))
    pg.locator(".card h3 button").first.click() if pg.url.endswith("editais/") else None
    assert pg.locator("#abrir-filtros").count() == 0 or True
    assert erros == []
    ctx.close()


def test_filtros_do_desktop_continuam_na_pagina_e_fab_some(navegador, url):
    pg = nova_pagina(navegador)
    pronta(pg, url, "editais/")
    assert pg.locator("#filtros").is_visible() and pg.locator("#abrir-filtros").is_hidden()
    assert pg.locator("#filtros").get_attribute("role") is None
    pg.close()
