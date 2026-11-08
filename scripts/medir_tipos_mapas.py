"""Mede, nas instâncias ativas de Mapas Culturais, o que há de oportunidade que NÃO é do tipo "Edital".

Serve para decidir com dados se vale ampliar `tipos_aceitos`. Lista os itens abertos e com prazo real
(os mesmos filtros do coletor) de cada tipo, marca os que um filtro de título de fomento aceitaria e imprime
a contagem. A precisão do filtro é conferida lendo a lista: este script não decide nada sozinho.

Uso: python -m scripts.medir_tipos_mapas [saida.json]     (uma consulta paginada por instância, como na coleta diária)
Resultado de 08/11/2026 (36 instâncias, 47 itens fora de "Edital" abertos): precisão ~40% (2 de 5) com o filtro abaixo;
ampliação descartada (ver DECISIONS.md).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from worker.coletores import mapas_culturais as mc
from worker.http import ClienteHttp
from worker.web import ClienteWeb  # noqa: F401  (mesmo User-Agent e regras do restante do worker)

FONTES = Path(__file__).resolve().parent.parent / "worker" / "fontes.yaml"
POSITIVO = re.compile(
    r"\b(edital|chamad[ao]|chamamento|credenciamento|premio|premiacao|bolsa|residencia|selecao|concurso)\b"
)
NEGATIVO = re.compile(
    r"\b(formulario|prestacao|cadastro|curso|oficina|aulas?|ficha|consulta|habilitacao|eleicao|pareceristas?|palestra|agenda|mapeamento)\b"
)


def sem_acento(t: str) -> str:
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn"
    )


def titulo_de_fomento(titulo: str) -> bool:
    t = sem_acento(titulo)
    return bool(POSITIVO.search(t)) and not NEGATIVO.search(t)


async def medir_instancia(h: ClienteHttp, inst: mc.Instancia, params: mc.Parametros) -> list[dict]:
    agora = datetime.now(UTC)
    local = agora.astimezone(mc.FUSO_PADRAO)
    q = {
        "@select": mc.CAMPOS, "@limit": "100", "@order": "registrationTo ASC", "status": "EQ(1)",
        "registrationTo": f"GTE({mc._filtro_data(local - timedelta(days=1))})",
        "registrationFrom": f"LTE({mc._filtro_data(local + timedelta(days=1))})",
    }  # fmt: skip
    saida: list[dict] = []
    for pagina in range(1, mc.MAX_PAGINAS + 1):
        itens = await h.json(
            f"{inst.base_url.rstrip('/')}/api/opportunity/find", {**q, "@page": str(pagina)}
        )
        for it in itens:
            tipo = (it.get("type") or {}).get("name")
            if tipo in params.tipos_aceitos:
                continue
            proprio = mc.Parametros(
                tipos_aceitos=[tipo],
                excluir_titulo_regex=params.excluir_titulo_regex,
                ano_sem_prazo_a_partir_de=params.ano_sem_prazo_a_partir_de,
            )
            if mc.converter(it, inst, proprio, agora, Counter()):
                titulo = it.get("name") or ""
                saida.append(
                    {
                        "inst": inst.slug,
                        "tipo": tipo,
                        "titulo": titulo,
                        "url": it.get("singleUrl"),
                        "filtro_aceitaria": titulo_de_fomento(titulo),
                    }
                )
        if len(itens) < 100:
            break
    return saida


async def main(destino: str | None) -> None:
    dados = yaml.safe_load(FONTES.read_text(encoding="utf-8"))
    params = mc.Parametros(**dados["parametros"])
    insts = [
        mc.Instancia(**i)
        for i in dados["instancias"]
        if i.get("ativa") and i.get("ambito", "brasil") == "brasil"
    ]
    async with ClienteHttp() as h:
        res = await asyncio.gather(
            *(medir_instancia(h, i, params) for i in insts), return_exceptions=True
        )
    itens = [x for r in res if isinstance(r, list) for x in r]
    falhas = [i.slug for i, r in zip(insts, res, strict=True) if isinstance(r, BaseException)]
    aceitos = [x for x in itens if x["filtro_aceitaria"]]
    print(
        f"{len(insts)} instâncias ({len(falhas)} com falha); {len(itens)} itens abertos fora de {params.tipos_aceitos}; o filtro de título aceitaria {len(aceitos)}"
    )
    print("por tipo:", Counter(x["tipo"] for x in itens).most_common())
    for x in aceitos:
        print(f"  ? {x['inst']:<14} {x['tipo']:<26} {x['titulo'][:90]}")
    if destino:
        Path(destino).write_text(json.dumps(itens, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
