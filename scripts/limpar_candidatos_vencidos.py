"""Fecha os candidatos (Issues `candidato`) cujo prazo de inscrição já passou.

O robô cria o candidato com o prazo no YAML; depois de passado o prazo ele não pode mais virar edital aberto, e deixá-lo na fila só
esconde os que ainda valem. Fecha com um comentário e como "não planejada" (não conta como aprovada).

Regras de segurança:
- só Issues abertas com o rótulo `candidato` (nunca `aprovado`, que é do fluxo de publicação);
- só com `inscricao_fim` legível no YAML; sem prazo, não mexe;
- só vencidos há pelo menos `MARGEM_DIAS` dias (Issues antigas criadas no Actions têm o prazo um dia adiantado, bug já corrigido);
- no máximo `MAXIMO_POR_EXECUCAO` por vez; erro numa Issue não impede as outras.

Uso: python -m scripts.limpar_candidatos_vencidos [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, date, datetime
from typing import Any

import httpx

from worker.fila import ROTULO_APROVADO, FilaGitHub, yaml_da_issue
from worker.padronizacao import FUSO_PADRAO

log = logging.getLogger("limpar_candidatos")
MARGEM_DIAS = 2
MAXIMO_POR_EXECUCAO = 100
COMENTARIO = (
    "Fechada automaticamente: o prazo de inscrição deste candidato ({prazo}) já passou, então ele não pode mais virar edital aberto "
    "no portal. Se o edital for reaberto ou prorrogado, o portal cria um novo candidato com o prazo atual."
)


def prazo_da_issue(issue: dict[str, Any]) -> date | None:
    dados = yaml_da_issue(issue.get("body") or "") or {}
    v = dados.get("inscricao_fim")
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def vencidas(
    issues: list[dict[str, Any]], hoje: date, margem: int = MARGEM_DIAS
) -> list[tuple[dict[str, Any], date]]:
    saida = []
    for i in issues:
        rotulos = {(x["name"] if isinstance(x, dict) else str(x)) for x in i.get("labels", [])}
        if ROTULO_APROVADO in rotulos:
            continue
        prazo = prazo_da_issue(i)
        if prazo and (hoje - prazo).days >= margem:
            saida.append((i, prazo))
    return sorted(saida, key=lambda x: x[1])


async def limpar(fila: FilaGitHub, hoje: date, dry_run: bool = False) -> list[int]:
    issues = await fila.candidatos()
    alvo = vencidas(issues, hoje)[:MAXIMO_POR_EXECUCAO]
    fechadas: list[int] = []
    for issue, prazo in alvo:
        n = int(issue["number"])
        if dry_run:
            log.info("(dry-run) fecharia #%d (prazo %s)", n, prazo)
            fechadas.append(n)
            continue
        try:
            r = await fila.fechar_issue(
                n, COMENTARIO.format(prazo=prazo.strftime("%d/%m/%Y")), motivo="not_planned"
            )
            if r.status_code >= 400:
                log.warning("não consegui fechar #%d: HTTP %d", n, r.status_code)
                continue
            fechadas.append(n)
        except httpx.HTTPError as exc:
            log.warning("não consegui fechar #%d: %s", n, exc)
    log.info(
        "%d candidatos abertos, %d vencidos, %d fechados", len(issues), len(alvo), len(fechadas)
    )
    return fechadas


async def principal(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dry-run", action="store_true", help="só lista o que fecharia")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    token, repo = os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("GITHUB_TOKEN/GITHUB_REPOSITORY ausentes: nada a fazer")
        return 0
    fila = FilaGitHub(repo, token)
    try:
        fechadas = await limpar(
            fila, datetime.now(UTC).astimezone(FUSO_PADRAO).date(), args.dry_run
        )
    except httpx.HTTPError as exc:
        log.error("não consegui listar os candidatos: %s", exc)
        return 1
    finally:
        await fila.fechar()
    print(
        f"{len(fechadas)} candidatos vencidos {'(dry-run) ' if args.dry_run else 'fechados'}: {fechadas}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
