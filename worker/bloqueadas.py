"""Semanal: abre uma Issue "Conferir manualmente: <fonte>" para cada fonte que bloqueia o robô.

Lê `fontes` do snapshot (status `bloqueada_em_ci`). Não contornamos bloqueio: uma pessoa abre o
link no navegador e, se houver edital novo, cria o candidato à mão.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from .catalogo import eh_falha_de_conexao
from .fila import API

SNAPSHOT = Path(__file__).resolve().parent.parent / "web" / "data" / "snapshot.json"
ROTULO = "conferir-manual"


async def principal() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("GITHUB_TOKEN/GITHUB_REPOSITORY ausentes: nada a fazer")
        return 0
    fontes = json.loads(SNAPSHOT.read_text(encoding="utf-8")).get("fontes", [])
    bloqueadas = [f for f in fontes if f.get("status") == "bloqueada_em_ci"]
    cab = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(base_url=API, headers=cab, timeout=30) as c:
        await c.post(
            f"/repos/{repo}/labels",
            json={"name": ROTULO, "color": "d93f0b", "description": "Fonte que bloqueia o robô"},
        )
        abertas = (
            await c.get(
                f"/repos/{repo}/issues",
                params={"labels": ROTULO, "state": "open", "per_page": "100"},
            )
        ).json()
        titulos = {i["title"] for i in abertas}
        for f in bloqueadas:
            titulo = f"Conferir manualmente: {f['nome']}"
            if titulo in titulos:
                continue
            if eh_falha_de_conexao(f.get("erro")):
                motivo = (
                    f"A fonte **{f['nome']}** não respondeu aos servidores do GitHub por vários dias seguidos "
                    f"({f.get('erro')}). Pode ser filtro de IP contra datacenters; o site pode estar normal no navegador."
                )
            else:
                motivo = f"A fonte **{f['nome']}** bloqueou o robô na última coleta ({f.get('erro') or 'sem detalhe'})."
            corpo = (
                f"{motivo}\n\n"
                f"Abra {f['url']} no navegador e confira se há edital novo. Se houver, crie um candidato.\n"
                "O portal não tenta contornar o bloqueio."
            )
            r = await c.post(
                f"/repos/{repo}/issues", json={"title": titulo, "body": corpo, "labels": [ROTULO]}
            )
            print(f"{titulo}: HTTP {r.status_code}")
    print(f"{len(bloqueadas)} fontes bloqueadas")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
