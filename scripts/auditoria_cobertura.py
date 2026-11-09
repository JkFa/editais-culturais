"""Auditoria de recall: quantos editais abertos de uma amostra independente o portal tem?

A amostra (`worker/referencia/amostra_recall.json`) é levantada FORA do pipeline do portal: cada item é um
edital aberto achado direto no site do órgão, com a URL oficial, o órgão, a UF, o prazo e a data da conferência.
Itens com prazo vencido saem da conta. O resultado vai para `worker/referencia/auditoria.json` (lido pelo placar) e,
com `GITHUB_TOKEN`, abre uma Issue `cobertura` listando o que faltou e a causa provável.

Uso: python -m scripts.auditoria_cobertura [--dry-run] [--amostra caminho] [--snapshot caminho]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from worker.dedup import similaridade_titulos
from worker.fila import normalizar_url
from worker.padronizacao import FUSO_PADRAO
from worker.taxonomia import sem_acento

log = logging.getLogger("auditoria_cobertura")
RAIZ = Path(__file__).resolve().parent.parent
AMOSTRA = RAIZ / "worker" / "referencia" / "amostra_recall.json"
SNAPSHOT = RAIZ / "web" / "data" / "snapshot.json"
SAIDA = RAIZ / "worker" / "referencia" / "auditoria.json"
LIMIAR_TITULO = 0.4


def _data(v: Any) -> date | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date() if v else None
    except ValueError:
        return None


def _urls(e: dict[str, Any]) -> set[str]:
    brutas = [e.get("url_origem"), e.get("url_inscricao"), e.get("url_edital_pdf")]
    for alt in e.get("fontes_alternativas") or []:
        brutas.append(alt.get("url") if isinstance(alt, dict) else alt)
    return {normalizar_url(u) for u in brutas if u}


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _dia_brt(v: Any) -> date | None:
    """Dia do prazo em Brasília (o prazo vem em UTC: 23h59 de Brasília já é o dia seguinte em UTC)."""
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
    except ValueError:
        return None
    return d.astimezone(FUSO_PADRAO).date() if d and d.tzinfo else (d.date() if d else None)


def _sem(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", sem_acento(texto or ""))


def encontrado(item: dict[str, Any], editais: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Mesma regra de deduplicação do pipeline: URL canônica igual, ou título parecido com o mesmo prazo.

    Itens dos diários oficiais (título genérico, link do espelho) também casam por UF + dia do prazo + município no título/órgão."""
    alvo = normalizar_url(item["url"])
    prazo = _data(item.get("prazo"))
    for e in editais:
        if alvo in _urls(e):
            return e
    cidade = _sem(item.get("municipio") or "")
    if item.get("uf") and prazo and len(cidade) >= 4:
        for e in editais:
            if e.get("uf") != item["uf"] or _dia_brt(e.get("inscricao_fim")) not in (prazo, None):
                continue
            if cidade in _sem(
                f"{e.get('titulo')} {e.get('orgao_fomento')} {e.get('municipio') or ''}"
            ):
                return e
    for e in editais:
        if prazo and _data(e.get("inscricao_fim")) not in (None, prazo):
            continue
        if similaridade_titulos(item["titulo"], e.get("titulo") or "") >= LIMIAR_TITULO:
            return e
    return None


def causa_provavel(item: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """Heurística para orientar a correção; a pessoa confirma ao abrir a Issue."""
    fontes = snapshot.get("fontes", [])
    if item.get("origem") == "querido-diario":
        return (
            "edital do diário oficial que o portal ainda não publica: está na fila de candidatos, "
            "sem link oficial legível ou sem passar nas regras"
        )
    host = _host(item["url"])
    do_mesmo_site = [f for f in fontes if _host(f.get("url") or "") == host]
    if any(f.get("ativa") and f.get("status") == "ok" for f in do_mesmo_site):
        return "o site é monitorado e o edital não veio: parser falhou ou o filtro descartou"
    if any(f.get("ativa") for f in do_mesmo_site):
        return "o site é monitorado, mas a fonte está com problema na última coleta"
    if do_mesmo_site:
        return "o site foi avaliado e está desativado (ver o motivo em fontes.yaml)"
    uf = item.get("uf")
    if uf and any(
        f.get("ativa") and f.get("status") == "ok" and f["fonte"] == f"mapas:{uf.lower()}"
        for f in fontes
    ):
        return (
            f"a UF {uf} tem Mapas Culturais, mas este edital está fora dele: fonte não monitorada"
        )
    return "fonte não monitorada"


def auditar(amostra: dict[str, Any], snapshot: dict[str, Any], hoje: date) -> dict[str, Any]:
    editais = [e for e in snapshot.get("editais", []) if e.get("ambito", "brasil") == "brasil"]
    abertos = [
        i for i in amostra.get("itens", []) if (p := _data(i.get("prazo"))) is None or p >= hoje
    ]
    achados, faltam = 0, []
    for i in abertos:
        if encontrado(i, editais):
            achados += 1
        else:
            faltam.append(
                {
                    "titulo": i["titulo"],
                    "url": i["url"],
                    "orgao": i.get("orgao"),
                    "uf": i.get("uf"),
                    "prazo": i.get("prazo"),
                    "causa": causa_provavel(i, snapshot),
                }
            )
    n = len(abertos)
    return {
        "data": hoje.isoformat(), "amostra": n, "encontrados": achados, "recall": round(achados / n, 4) if n else None,
        "descartados_por_prazo": len(amostra.get("itens", [])) - n, "faltantes": faltam,
        "metodologia": amostra.get("metodologia"), "universo": amostra.get("universo"),
        "origens": {o: sum(1 for i in abertos if (i.get("origem") or "manual") == o) for o in {(i.get("origem") or "manual") for i in abertos}},
    }  # fmt: skip


def corpo_da_issue(r: dict[str, Any]) -> str:
    linhas = [
        f"Recall da auditoria de {r['data']}: **{r['encontrados']} de {r['amostra']}** editais abertos da amostra de referência "
        f"({(r['recall'] or 0):.0%}). Meta: 90%.",
        "",
    ]
    if r["amostra"] < 30:
        linhas += [
            '> A amostra tem menos de 30 editais abertos: o placar **não usa** este número (recall "não medido").',
            "",
        ]
    if r["faltantes"]:
        linhas += ["| Edital | Órgão | UF | Prazo | Causa provável |", "|---|---|---|---|---|"]
        for f in r["faltantes"]:
            linhas.append(
                f"| [{f['titulo']}]({f['url']}) | {f.get('orgao') or ''} | {f.get('uf') or ''} | {f.get('prazo') or ''} | {f['causa']} |"
            )
    else:
        linhas.append("Nenhum edital faltando.")
    return "\n".join(linhas)


def abrir_issue(repo: str, token: str, r: dict[str, Any]) -> str:
    cab = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(base_url="https://api.github.com", headers=cab, timeout=30) as c:
        resp = c.post(f"/repos/{repo}/issues", json={
            "title": f"Auditoria de cobertura {r['data'][:7]}: recall {(r['recall'] or 0):.0%} ({r['encontrados']}/{r['amostra']})",
            "body": corpo_da_issue(r), "labels": ["cobertura"],
        })  # fmt: skip
        resp.raise_for_status()
        return resp.json()["html_url"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run", action="store_true", help="não grava auditoria.json nem abre Issue"
    )
    ap.add_argument("--amostra", type=Path, default=AMOSTRA)
    ap.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    ap.add_argument("--saida", type=Path, default=SAIDA)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        amostra = json.loads(args.amostra.read_text(encoding="utf-8"))
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.error("não consegui ler a amostra ou o snapshot: %s", exc)
        return 2
    r = auditar(amostra, snapshot, datetime.now(UTC).date())
    print(corpo_da_issue(r))
    if args.dry_run:
        return 0
    args.saida.write_text(json.dumps(r, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if token and repo:
        log.info("Issue: %s", abrir_issue(repo, token, r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
