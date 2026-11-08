"""Auxiliares dos testes ponta a ponta (Playwright). As fixtures vivem no conftest."""

from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
WEB = RAIZ / "web"
SAIDA = RAIZ / "verificacao"
HOSTS_DE_FONTE = ("fonts.googleapis.com", "fonts.gstatic.com")


def abertos(snapshot):
    """Editais abertos do Brasil: o que a tela padrão mostra (as oportunidades de fora têm filtro próprio)."""
    return [e for e in abertos_todos(snapshot) if e.get("ambito", "brasil") == "brasil"]


def abertos_todos(snapshot):
    agora = datetime.now(UTC)
    return [e for e in snapshot["editais"] if datetime.fromisoformat(e["inscricao_fim"]) >= agora]


def so_do_brasil(snapshot):
    """Snapshot sem as oportunidades internacionais: base estável para testes que as acrescentam."""
    return {
        **snapshot,
        "editais": [e for e in snapshot["editais"] if e.get("ambito", "brasil") == "brasil"],
    }


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
    """Abre o explorador (/editais/)."""
    pg.goto(url + "editais/" + busca)
    pg.wait_for_selector("html[data-pronto]")
