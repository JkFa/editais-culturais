"""Monta a amostra de referência do recall a partir dos diários oficiais (Querido Diário), com termos DIFERENTES dos da coleta.

O recall só vale se a amostra for independente do que o portal coleta. Aqui ela vem dos diários oficiais municipais, buscados com
termos que o coletor (`worker/coletores/querido_diario.py`) NÃO usa (`TERMOS_AUDITORIA`), e cada item só entra se a passagem do edital
tiver pedido de inscrição, cultura no texto e **prazo futuro com o trecho literal** (as mesmas regras do coletor para ler o diário).

Limite assumido e publicado em /sobre/: o recall é medido sobre o universo dos diários dos municípios que o Querido Diário cobre (~510),
não sobre o Brasil inteiro. Mesmo assim, é uma medida real, e não uma suposição.

Os itens manuais (levantados à mão) são preservados; os do Querido Diário são refeitos a cada execução (os prazos vencem).

Uso: python -m scripts.amostra_recall_qd [--dry-run] [--max-diarios 400]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from worker.coletores import querido_diario as qd
from worker.padronizacao import FUSO_PADRAO
from worker.taxonomia import sem_acento
from worker.web import ClienteWeb, ErroWeb

log = logging.getLogger("amostra_recall_qd")
RAIZ = Path(__file__).resolve().parent.parent
AMOSTRA = RAIZ / "worker" / "referencia" / "amostra_recall.json"
ORIGEM = "querido-diario"
# Nenhum destes termos pode estar em `qd.TERMOS` (o teste confere): a amostra precisa ser independente da coleta.
TERMOS_AUDITORIA = [
    '"termo de execução cultural"', '"seleção de projetos culturais"', '"inscrições abertas" cultura',
    '"edital de premiação"', '"fomento à cultura" inscrições', "credenciamento artistas",
]  # fmt: skip
JANELA_DIAS = 21
MAX_DIARIOS = 400  # medido em 08/11/2026: 14 dias = 356 diários, 120 lidos = 10 editais abertos; a amostra precisa de 30
UNIVERSO = "diários oficiais dos municípios cobertos pelo Querido Diário (cerca de 510 municípios)"


def _chave(item: dict[str, Any]) -> str:
    """Mesmo município + mesmo número de edital = o mesmo edital (citado duas vezes, ou prorrogado). Sem número, o título."""
    numeros = (
        "|".join(sorted(qd.numeros_do_edital(item["titulo"])))
        or re.sub(r"[^a-z0-9]+", "", sem_acento(item["titulo"]))[:50]
    )
    return f"{item.get('uf')}|{sem_acento(item.get('municipio') or '')}|{numeros}"


def item_do_diario(g: dict[str, Any], p: qd.Passagem, hoje: datetime) -> dict[str, Any] | None:
    uf, municipio = qd._uf_e_municipio(g)
    if not municipio or not uf:
        return None
    return {
        "titulo": p.titulo, "url": p.link or g.get("url") or g["txt_url"], "orgao": f"Município de {municipio}", "uf": uf,
        "municipio": municipio, "prazo": p.prazo.fim.astimezone(FUSO_PADRAO).date().isoformat(),
        "conferido_em": hoje.date().isoformat(), "trecho": p.prazo.trecho[:200], "origem": ORIGEM,
        "diario": {"url": g.get("url"), "data": g.get("date")},
    }  # fmt: skip


async def levantar(
    cliente: ClienteWeb, hoje: datetime, max_diarios: int = MAX_DIARIOS
) -> list[dict[str, Any]]:
    bases = list(qd.API_BASES)
    desde = (hoje.astimezone(FUSO_PADRAO).date() - timedelta(days=JANELA_DIAS)).isoformat()
    achadas: dict[str, dict[str, Any]] = {}
    falhas = 0
    for termo in TERMOS_AUDITORIA:
        for pagina in range(qd.MAX_PAGINAS_POR_TERMO):
            params = {"querystring": termo, "published_since": desde, "size": qd.POR_PAGINA, "offset": pagina * qd.POR_PAGINA,
                      "excerpt_size": 700, "number_of_excerpts": 3, "sort_by": "descending_date"}  # fmt: skip
            try:
                dados = await qd._json(cliente, bases, "/gazettes", params, 2.0)
            except qd.ErroApi as exc:
                falhas += 1
                log.error("consulta %s falhou: %s", termo, exc)
                break
            lote = dados.get("gazettes") or []
            for g in lote:
                alvo = achadas.setdefault(g["txt_url"], {**g, "excerpts": []})
                alvo["excerpts"] += [
                    e for e in (g.get("excerpts") or []) if e not in alvo["excerpts"]
                ]
            if len(lote) < qd.POR_PAGINA:
                break
    if falhas == len(TERMOS_AUDITORIA):
        raise qd.ErroApi("nenhuma consulta respondeu")

    def nota(g: dict[str, Any]) -> int:
        return max(
            (2 if (c := qd.classificar_trecho(e)) == "abertura" else 1 if c == "posterior" else 0)
            for e in g["excerpts"] or [""]
        )

    triagem = [
        g
        for g in sorted(achadas.values(), key=lambda g: (nota(g), g["date"]), reverse=True)
        if nota(g) > 0
    ][:max_diarios]
    itens: dict[str, dict[str, Any]] = {}
    for g in triagem:
        try:
            r = await cliente.obter(g["txt_url"], intervalo_s=2.0)
        except ErroWeb:
            continue
        for p in qd.achar_passagens(r.texto, hoje):
            item = item_do_diario(g, p, hoje)
            if item:
                antigo = itens.get(_chave(item))
                if (
                    antigo is None or item["prazo"] > antigo["prazo"]
                ):  # prorrogado: vale o prazo mais novo
                    itens[_chave(item)] = item
    log.info(
        "%d diários na janela, %d lidos, %d editais abertos na amostra",
        len(achadas),
        len(triagem),
        len(itens),
    )
    return sorted(itens.values(), key=lambda i: (i["prazo"], i["uf"], i["municipio"]))


def mesclar(amostra: dict[str, Any], novos: list[dict[str, Any]], hoje: datetime) -> dict[str, Any]:
    """Mantém os itens manuais, troca todos os do Querido Diário pelos novos e atualiza a metodologia."""
    manuais = [i for i in amostra.get("itens", []) if i.get("origem") != ORIGEM]
    return {
        **amostra,
        "metodologia": (
            "Duas origens, ambas com prazo e trecho literal: (1) itens manuais, levantados nos sites oficiais; (2) itens dos diários oficiais "
            f"municipais lidos pelo Querido Diário com termos DIFERENTES dos da coleta, refeitos a cada auditoria. Universo medido: {UNIVERSO}. "
            "Prosas não é fonte. Itens com prazo vencido saem da conta."
        ),
        "universo": UNIVERSO,
        "gerado_em": hoje.isoformat(timespec="seconds"),
        "itens": manuais + novos,
    }


async def principal(argv: list[str] | None = None, caminho: Path = AMOSTRA) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dry-run", action="store_true", help="não grava o arquivo")
    ap.add_argument("--max-diarios", type=int, default=MAX_DIARIOS)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    hoje = datetime.now(UTC)
    async with ClienteWeb() as cliente:
        try:
            novos = await levantar(cliente, hoje, args.max_diarios)
        except qd.ErroApi as exc:
            log.error("Querido Diário fora do ar, amostra anterior mantida: %s", exc)
            return 1
    amostra = json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}
    corpo = mesclar(amostra, novos, hoje)
    print(f"{len(novos)} editais abertos dos diários + {len(corpo['itens']) - len(novos)} manuais")
    if not args.dry_run:
        caminho.write_text(json.dumps(corpo, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
