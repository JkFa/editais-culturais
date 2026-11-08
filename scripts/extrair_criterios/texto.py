"""Normalização de texto e verificação de trechos (a regra de integridade da extração)."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

MIN_TRECHO = 12  # caracteres normalizados: trecho menor que isso prova pouco


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos, sem hifenização de fim de linha, só letras/dígitos/%/$ e espaço simples."""
    t = texto.replace("­", "").replace("-\n", "").replace("‑\n", "")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9%$]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def trecho_existe(trecho: str, texto_normalizado: str) -> bool:
    """O trecho (citação do modelo) existe no texto do edital? Reticências separam fragmentos, que
    precisam aparecer na ordem. Comparação normalizada: ignora acento, caixa, pontuação e quebra de linha."""
    fragmentos = [normalizar(f) for f in re.split(r"…|\.{3,}", trecho)]
    fragmentos = [f for f in fragmentos if f]
    if not fragmentos or sum(len(f) for f in fragmentos) < MIN_TRECHO:
        return False
    posicao = 0
    for f in fragmentos:
        achado = texto_normalizado.find(f, posicao)
        if achado < 0:
            return False
        posicao = achado + len(f)
    return True


_NUM = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?|\d+\.\d+)(\s*(?:%|por cento|mil\b|milh(?:ao|oes|ão|ões)\b))?",
    re.I,
)


def numeros_do_trecho(trecho: str) -> list[tuple[Decimal, str]]:
    """Números do trecho como (valor, unidade), onde unidade é '', '%', 'mil' ou 'milhao'."""
    saida: list[tuple[Decimal, str]] = []
    for m in _NUM.finditer(trecho):
        bruto, sufixo = m.group(1), (m.group(2) or "").strip().lower()
        try:
            if "," in bruto:
                valor = Decimal(bruto.replace(".", "").replace(",", "."))
            elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", bruto):
                valor = Decimal(bruto.replace(".", ""))
            else:
                valor = Decimal(bruto)
        except InvalidOperation:
            continue
        unidade = (
            "%"
            if sufixo in ("%", "por cento")
            else "mil"
            if sufixo == "mil"
            else "milhao"
            if sufixo.startswith("milh")
            else ""
        )
        saida.append((valor, unidade))
    return saida


def trecho_comprova_numero(valor: float | int, trecho: str, *, percentual: bool = False) -> bool:
    """O número do campo aparece no trecho? Para percentual, o campo é fração (0,15) e o texto, '15%'."""
    alvo = Decimal(str(valor))
    for n, unidade in numeros_do_trecho(trecho):
        candidatos = [n]
        if unidade == "mil":
            candidatos.append(n * 1000)
        elif unidade == "milhao":
            candidatos.append(n * 1_000_000)
        if percentual:
            candidatos = [c / 100 for c in candidatos] + (
                [c for c in candidatos if c <= 1] if unidade != "%" else []
            )
        if any(abs(c - alvo) < Decimal("0.0051") for c in candidatos):
            return True
    return False


def slug(texto: str, limite: int = 40) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", normalizar(texto).replace("%", "").replace("$", "")).strip(
        "_"
    )
    return (base or "criterio")[:limite].strip("_") or "criterio"
