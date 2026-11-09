"""Registro semanal dos municípios cobertos pelo Querido Diário (`worker/referencia/querido_diario_municipios.json`).

O Querido Diário só coleta os diários de parte dos municípios (hoje ~510, com texto completo). Para o placar de cobertura, o município
coberto conta como "monitorado" com meio ponto: o portal lê o diário dele todo dia, mas só acha o edital se o diário o publicar e o
texto casar com as buscas. Esta lista (nível 3 = texto completo disponível) é refeita toda semana.

Se a consulta falhar, o arquivo anterior é mantido (lista parcial subestimaria a cobertura) e o script sai com erro.

Uso: python -m scripts.municipios_querido_diario [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.coletores.querido_diario import API_BASES, ErroApi, _json
from worker.web import ClienteWeb

log = logging.getLogger("municipios_querido_diario")
SAIDA = (
    Path(__file__).resolve().parent.parent
    / "worker"
    / "referencia"
    / "querido_diario_municipios.json"
)
NIVEL_TEXTO_COMPLETO = "3"
MINIMO_PLAUSIVEL = 100  # a API devolver menos que isso é erro dela, não queda de cobertura


def cobertos(cidades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Municípios com texto completo disponível (nível 3 e data de início da coleta)."""
    saida = [
        {"ibge": int(c["territory_id"]), "nome": c["territory_name"], "uf": c["state_code"], "desde": c["availability_date"]}
        for c in cidades
        if str(c.get("level")) == NIVEL_TEXTO_COMPLETO and c.get("availability_date")
    ]  # fmt: skip
    return sorted(saida, key=lambda m: m["ibge"])


async def principal(argv: list[str] | None = None, saida: Path = SAIDA) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="não grava o arquivo")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    bases = list(API_BASES)
    async with ClienteWeb() as cliente:
        try:
            dados = await _json(cliente, bases, "/cities", {}, 2.0)
        except ErroApi as exc:
            log.error("API do Querido Diário fora do ar, arquivo anterior mantido: %s", exc)
            return 1
    cidades = dados.get("cities") or []
    lista = cobertos(cidades)
    if len(cidades) < MINIMO_PLAUSIVEL or not lista:
        log.error(
            "resposta implausível (%d municípios, %d cobertos): arquivo anterior mantido",
            len(cidades),
            len(lista),
        )
        return 1
    corpo = {
        "atualizado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "fonte": "https://queridodiario.ok.org.br/api/cities (nível 3: texto completo)",
        "total_na_api": len(cidades),
        "municipios": lista,
    }
    print(f"{len(lista)} municípios com texto completo, de {len(cidades)} na API")
    if not args.dry_run:
        saida.write_text(json.dumps(corpo, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
