"""Prazo de inscrição lido de texto corrido, sempre com o trecho que o comprova.

Só aceita datas com ano explícito: "até 30/04" sem ano é ambíguo e fica de fora (o edital
vira candidato para conferência humana em vez de ser publicado com data adivinhada).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from .padronizacao import FUSO_PADRAO
from .taxonomia import sem_acento

_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}  # fmt: skip
_NUM = r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})"
_EXTENSO = r"(\d{1,2})\s*(?:º|o|°)?\s+de\s+([a-zç]+)\s+de\s+(\d{4})"
_HORA = r"(?:\s*,?\s*(?:[aà]s|a partir das|ate as)?\s*(\d{1,2})\s*(?:h|:)\s*(\d{2})?)?"
_JANELA = 140
# Fases do cronograma que vêm DEPOIS das inscrições. A janela de busca para ali, senão a última data de uma tabela
# ("Inscrições 26/10 a 31/10 | Análise das inscrições e avaliação 03/11 a 09/11") viraria o prazo.
_FASE = re.compile(
    r"\b(analise|avaliacao|resultado|homologacao|recurso|recursos|divulgacao|habilitacao|classificacao|assinatura|contratacao|"
    r"pagamento|execucao|prestacao|julgamento|selecao dos|selecao (?=\d)|comissao)\b"
)


@dataclass(frozen=True)
class Prazo:
    fim: datetime  # UTC
    tem_horario: bool
    trecho: str
    inicio: datetime | None = None


def _data(m: re.Match[str], extenso: bool) -> date | None:
    try:
        if extenso:
            mes = _MESES.get(sem_acento(m.group(2)))
            return date(int(m.group(3)), mes, int(m.group(1))) if mes else None
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _datas_em(trecho: str) -> list[tuple[date, int, int | None, int | None]]:
    """(data, posição final, hora, minuto) de cada data com ano no trecho."""
    achados = []
    for rx, ext in ((_NUM, False), (_EXTENSO, True)):
        for m in re.finditer(rx + _HORA, trecho, re.IGNORECASE):
            d = _data(m, ext)
            if d:
                h = int(m.group(4)) if m.group(4) else None
                mi = int(m.group(5)) if m.group(5) else (0 if h is not None else None)
                achados.append((d, m.end(), h, mi))
    return sorted(achados, key=lambda x: x[1])


def _montar(d: date, h: int | None, mi: int | None) -> tuple[datetime, bool]:
    so_dia = h is None or (h, mi or 0) in ((0, 0), (23, 59))
    hora = time(23, 59, 59) if so_dia else time(h, mi or 0)  # type: ignore[arg-type]
    return datetime.combine(d, hora, tzinfo=FUSO_PADRAO).astimezone(UTC), not so_dia


def achar_prazo(texto: str) -> Prazo | None:
    """Fim (e início, se houver) das inscrições. `None` se o texto não permitir afirmar."""
    norm = sem_acento(texto)  # mesmo comprimento: offsets valem no texto original

    def janela(inicio: int) -> tuple[str, str]:
        return texto[inicio : inicio + _JANELA], norm[inicio : inicio + _JANELA]

    inicio_dt: datetime | None = None
    for m in re.finditer(r"inicio d[ao]s? inscri\w+", norm):
        trecho, _ = janela(m.end())
        ds = _datas_em(trecho)
        if ds:
            inicio_dt = datetime.combine(ds[0][0], time(0, 0), tzinfo=FUSO_PADRAO).astimezone(UTC)
            break

    # 1) "Encerramento do prazo de inscrição" (tabelas de cronograma)
    for m in re.finditer(r"encerramento d[ao]s? (?:prazo de )?inscri\w+", norm):
        trecho, _ = janela(m.end())
        ds = _datas_em(trecho)
        if ds:
            fim, hor = _montar(ds[0][0], ds[0][2], ds[0][3])
            return Prazo(
                fim, hor, texto[m.start() : m.end() + ds[0][1]].replace("\n", " "), inicio_dt
            )
    # 2) "inscrições ... de X a Y" / "até Y"
    for m in re.finditer(r"inscri\w+|prazo", norm):
        if _FASE.search(norm[max(0, m.start() - 30) : m.start()]):
            continue  # "análise das inscrições", "recurso das inscrições": não é o pedido de inscrição
        trecho, trecho_norm = janela(m.end())
        corte = _FASE.search(trecho_norm)
        if corte:
            trecho, trecho_norm = trecho[: corte.start()], trecho_norm[: corte.start()]
        ate = re.search(r"\b(?:ate|a|ao|e)\b|—|-|–", trecho_norm)
        if not ate:
            continue
        ds = _datas_em(trecho)
        if ds and (ate.start() < ds[-1][1]):
            d, fim_pos, h, mi = ds[-1]
            if len(ds) == 1 and not re.search(
                r"\bate\b|encerr|termin|\ba\s+\d", trecho_norm[:fim_pos]
            ):
                continue
            fim, hor = _montar(d, h, mi)
            return Prazo(
                fim, hor, texto[m.start() : m.end() + fim_pos].replace("\n", " "), inicio_dt
            )
    return None
