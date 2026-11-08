"""Deduplicação entre fontes: um registro canônico por edital, demais origens em `fontes_alternativas`.

Duas ofertas são o mesmo edital quando (1) compartilham alguma URL normalizada (página, inscrição ou
PDF) ou (2) têm o mesmo prazo final no mesmo dia e títulos muito parecidos (Jaccard das palavras de
conteúdo ≥ 0,4 e ao menos 3 em comum). Só entre fontes diferentes, e sem prazo igual nunca juntamos só pelo título: edital "2026" e "2027" têm títulos
quase iguais.

Preferência para ficar como canônico: curadoria > oficial > plataforma > descoberta.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .fila import normalizar_url
from .modelos import Edital
from .taxonomia import sem_acento

_PRIORIDADE_TIPO = {"curadoria": 0, "oficial": 1, "plataforma": 2, "descoberta": 3}
_VAZIAS = {
    "de", "da", "do", "das", "dos", "e", "em", "para", "com", "a", "o", "as", "os", "no", "na", "nos",
    "nas", "ao", "aos", "edital", "chamamento", "publico", "programa", "selecao", "n", "nº",
    "ciclo", "fomento", "cultura", "cultural", "culturais", "projetos", "politica", "nacional",
    "aldir", "blanc", "lei", "federal", "recursos", "execucao", "termo",
}  # fmt: skip


def _singular(p: str) -> str:
    return re.sub(r"(es|s)$", "", p) if len(p) > 4 else p


_VAZIAS_SING = {_singular(x) for x in _VAZIAS} | {"pnab", "lpg", "sec", "smc"}
LIMIAR_TITULO = 0.4
MIN_PALAVRAS_COMUNS = 3


def _urls(e: Edital) -> set[str]:
    return {normalizar_url(u) for u in (e.url_origem, e.url_inscricao, e.url_edital_pdf) if u}


def _palavras(titulo: str) -> set[str]:
    """Palavras de conteúdo: sem número (edital 001, 2026), sigla de UF, "pnab" e palavras vazias."""
    palavras = {_singular(p) for p in re.findall(r"[a-z0-9]+", sem_acento(titulo))}
    return {p for p in palavras if len(p) > 2 and not p.isdigit() and p not in _VAZIAS_SING}


def similaridade_titulos(a: str, b: str) -> float:
    pa, pb = _palavras(a), _palavras(b)
    if len(pa & pb) < MIN_PALAVRAS_COMUNS:
        return 0.0
    return len(pa & pb) / len(pa | pb)


def mesmo_edital(a: Edital, b: Edital) -> bool:
    if _urls(a) & _urls(b):
        return True
    # Dentro da mesma fonte cada registro é um edital diferente (os "Funarte Aberta" de cada espaço
    # têm título e prazo quase iguais): só juntamos ofertas de fontes distintas.
    if a.fonte == b.fonte:
        return False
    if a.inscricao_fim.date() != b.inscricao_fim.date():
        return False
    return similaridade_titulos(a.titulo, b.titulo) >= LIMIAR_TITULO


def _ordem(e: Edital, tipos: dict[str, str]) -> tuple[int, int, str]:
    tipo = (
        "curadoria"
        if e.fonte in ("curadoria", "fila", "curadoria-internacional")
        else tipos.get(e.fonte, "plataforma")
    )
    completo = sum(
        x is not None for x in (e.valor_total, e.teto_por_projeto, e.proponentes, e.url_edital_pdf)
    )
    return _PRIORIDADE_TIPO.get(tipo, 2), -completo, e.fonte


def deduplicar(
    editais: Iterable[Edital], tipos_por_fonte: dict[str, str]
) -> tuple[list[Edital], int]:
    """(lista sem duplicatas, quantidade removida). Fonte->tipo vem do catálogo."""
    lista = list(editais)
    grupos: list[list[Edital]] = []
    for e in lista:
        # Um grupo nunca tem duas ofertas da mesma fonte: cada uma é um edital diferente.
        alvo = next(
            (
                g
                for g in grupos
                if all(x.fonte != e.fonte for x in g) and any(mesmo_edital(e, x) for x in g)
            ),
            None,
        )
        if alvo is None:
            grupos.append([e])
        else:
            alvo.append(e)
    saida: list[Edital] = []
    removidos = 0
    for g in grupos:
        g.sort(key=lambda x: _ordem(x, tipos_por_fonte))
        canonico, resto = g[0], g[1:]
        if resto:
            removidos += len(resto)
            alternativas = list(canonico.fontes_alternativas or [])
            vistos = {(a.get("fonte"), a.get("url")) for a in alternativas}
            for r in resto:
                chave = (r.fonte, r.url_origem)
                if chave not in vistos:
                    alternativas.append({"fonte": r.fonte, "url": r.url_origem, "via": r.via})
                    vistos.add(chave)
            canonico = canonico.model_copy(update={"fontes_alternativas": alternativas})
            canonico = canonico.model_copy(update={"hash_conteudo": canonico.calcular_hash()})
        saida.append(canonico)
    return saida, removidos
