"""Gera og.png (1200x630) e os ícones PNG a partir dos SVG, com o Chromium do Playwright.
Uso: python scripts/gerar_imagens.py  (precisa de `playwright install chromium` e rede para as fontes)."""

from pathlib import Path

from playwright.sync_api import sync_playwright

IMG = Path(__file__).resolve().parent.parent / "web" / "assets" / "img"
FONTES = (
    "https://fonts.googleapis.com/css2?family=Inter:wght@400&family=Instrument+Serif"
    "&family=JetBrains+Mono:wght@400&display=block"
)


def renderizar(pg, svg: Path, saida: Path, tamanho: tuple[int, int]) -> None:
    pg.set_viewport_size({"width": tamanho[0], "height": tamanho[1]})
    corpo = svg.read_text(encoding="utf-8")
    pg.set_content(
        f'<html><head><link rel="stylesheet" href="{FONTES}"></head>'
        f'<body style="margin:0">{corpo.replace("<svg ", f"<svg width={tamanho[0]} height={tamanho[1]} ", 1)}</body></html>'
    )
    pg.wait_for_load_state("networkidle")
    pg.evaluate("document.fonts.ready")
    pg.screenshot(path=str(saida), clip={"x": 0, "y": 0, "width": tamanho[0], "height": tamanho[1]})


with sync_playwright() as p:
    nav = p.chromium.launch()
    pg = nav.new_page()
    renderizar(pg, IMG / "og.svg", IMG / "og.png", (1200, 630))
    for n in (180, 192, 512):
        renderizar(pg, IMG / "icone.svg", IMG / f"icone-{n}.png", (n, n))
    nav.close()
print("imagens geradas em", IMG)
