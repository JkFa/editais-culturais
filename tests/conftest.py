import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def carregar(nome: str) -> list[dict]:
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


def todos_os_itens() -> list[dict]:
    itens: list[dict] = []
    for arquivo in sorted(FIXTURES.glob("mapas_*.json")):
        itens += json.loads(arquivo.read_text(encoding="utf-8"))
    return itens


@pytest.fixture(scope="session")
def itens_reais() -> list[dict]:
    return todos_os_itens()


@pytest.fixture(scope="session")
def textos_reais(itens_reais) -> list[str]:
    from worker.padronizacao import limpar_texto

    from worker.extrator import extrair_pagina

    textos = [
        limpar_texto(f"{i.get('shortDescription') or ''} {i.get('longDescription') or ''}")
        for i in itens_reais
    ]
    # Páginas reais do MinC (v4) também servem de corpus para o regex de valores.
    for arquivo in sorted((FIXTURES / "v4").glob("minc_detalhe_*.html")):
        textos.append(limpar_texto(extrair_pagina(arquivo.read_text(encoding="utf-8")).texto))
    return textos


# ---------------------------------------------------------------- fixtures do e2e (Playwright)

import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "web"


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
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", porta), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{porta}/"
    proc.terminate()


@pytest.fixture(scope="module")
def navegador():
    playwright_sync = pytest.importorskip("playwright.sync_api")
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
