"""Registro semanal dos entes federados que têm edital no CultBR (`worker/referencia/cultbr_entes.json`).

O coletor diário (`worker/coletores/cultbr.py`) só lê os editais com `status=Aberto`. Este script percorre a lista
inteira, uma vez por semana, só para saber QUAIS entes (municípios e estados) usam o CultBR. O placar de cobertura
conta o município com edital no CultBR como "monitorado", com meio ponto (o município pode publicar também em outros
lugares que o portal não lê).

Respeita o `robots.txt`, usa o User-Agent do portal e 3 s entre páginas (ClienteWeb). Se alguma página falhar depois
das novas tentativas, NÃO sobrescreve o arquivo anterior (dado parcial subestimaria a cobertura) e sai com erro.

Uso: python -m scripts.entes_cultbr [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.web import ClienteWeb, ErroWeb

log = logging.getLogger("entes_cultbr")
API = "https://back.portal.cultbr.cultura.gov.br/v1/public/par/editais"
SAIDA = Path(__file__).resolve().parent.parent / "worker" / "referencia" / "cultbr_entes.json"
POR_PAGINA = 100
INTERVALO_S = 3.0
TENTATIVAS = 3


def tipo_do_ente(nome: str) -> str | None:
    n = (nome or "").upper().strip()
    if n.startswith("MUNIC"):
        return "municipio"
    if n.startswith("ESTADO") or n.startswith("DISTRITO FEDERAL"):
        return "estado"
    return None


def agregar(itens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Um registro por (tipo, UF, município): quantos editais tem no CultBR e quantos estão com status Aberto."""
    por_ente: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"editais": 0, "abertos": 0, "ano_par": 0}
    )
    for i in itens:
        tipo = tipo_do_ente(i.get("nome_ente_recebedor") or "")
        uf = (i.get("uf_ente_recebedor_plano_acao") or "").upper()
        if not tipo or not uf:
            continue
        mun = (
            (i.get("nome_municipio_ente_recebedor_plano_acao") or "").strip()
            if tipo == "municipio"
            else ""
        )
        if tipo == "municipio" and not mun:
            continue
        e = por_ente[(tipo, uf, mun)]
        e["editais"] += 1
        e["abertos"] += int(i.get("status") == "Aberto")
        try:
            e["ano_par"] = max(e["ano_par"], int(i.get("ano_par") or 0))
        except (TypeError, ValueError):
            pass
    return [
        {"tipo": t, "uf": uf, "municipio": mun or None, **dados}
        for (t, uf, mun), dados in sorted(por_ente.items())
    ]  # fmt: skip


async def baixar_todos(cliente: ClienteWeb) -> list[dict[str, Any]]:
    itens: list[dict[str, Any]] = []
    pagina, total = 1, 1
    while pagina <= total:
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                r = await cliente.obter(
                    f"{API}?per_page={POR_PAGINA}&page={pagina}", intervalo_s=INTERVALO_S
                )
                dados = json.loads(r.texto)
                break
            except (ErroWeb, ValueError) as exc:
                log.warning("página %d, tentativa %d/%d: %s", pagina, tentativa, TENTATIVAS, exc)
                if tentativa == TENTATIVAS:
                    raise
                await asyncio.sleep(5 * tentativa)
        total = int(dados.get("total_pages", 1))
        itens += dados.get("items", [])
        pagina += 1
    return itens


async def principal(argv: list[str] | None = None, saida: Path = SAIDA) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="não grava o arquivo")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    async with ClienteWeb() as cliente:
        try:
            itens = await baixar_todos(cliente)
        except (ErroWeb, ValueError) as exc:
            log.error("lista incompleta, arquivo anterior mantido: %s", exc)
            return 1
    entes = agregar(itens)
    corpo = {
        "atualizado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "fonte": API,
        "total_registros": len(itens),
        "entes": entes,
    }
    municipios = sum(1 for e in entes if e["tipo"] == "municipio")
    print(
        f"{len(itens)} registros; {municipios} municípios e {len(entes) - municipios} estados com edital no CultBR"
    )
    if not args.dry_run:
        saida.write_text(json.dumps(corpo, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
