"""Planejamento da fila diária (puro, sem rede): quem entra, quem é processado hoje e quem espera."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

MAX_TENTATIVAS = 3
REVERIFICAR_DIAS = (
    30  # o documento é conferido de novo (1 download, sem LLM se o hash for igual) a cada tanto
)
PROCESSANDO_TRAVADO_H = (
    2  # execução que caiu no meio deixa `processando`; depois disso volta para a fila
)


def url_do_documento(edital: dict[str, Any]) -> str | None:
    """PDF do edital quando existe; senão a página do edital."""
    return edital.get("url_edital_pdf") or edital.get("url_origem") or None


@dataclass
class Plano:
    enfileirar: list[int] = field(
        default_factory=list
    )  # editais abertos que ainda não estão na fila
    processar: list[dict[str, Any]] = field(default_factory=list)
    elegiveis: int = 0

    @property
    def restantes(self) -> int:
        return self.elegiveis - len(self.processar)


def planejar(
    editais: list[dict[str, Any]],
    fila: dict[int, dict[str, Any]],
    criterios: dict[int, dict[str, Any]],
    projetos: Counter[int],
    agora: datetime,
    max_por_dia: int,
) -> Plano:
    plano = Plano()
    elegiveis: list[dict[str, Any]] = []
    for e in editais:
        linha = fila.get(e["id"])
        if linha is None:
            plano.enfileirar.append(e["id"])
            elegiveis.append(e)
            continue
        status = linha.get("status")
        atualizado = linha.get("atualizado_em")
        velho = atualizado is None or atualizado < agora - timedelta(days=REVERIFICAR_DIAS)
        if status == "pendente":
            elegivel = True
        elif status == "erro":
            elegivel = int(linha.get("tentativas") or 0) < MAX_TENTATIVAS
        elif status == "processando":
            elegivel = atualizado is None or atualizado < agora - timedelta(
                hours=PROCESSANDO_TRAVADO_H
            )
        else:  # ok | sem_documento
            c = criterios.get(e["id"])
            url_mudou = bool(c and c.get("fonte_url") and c["fonte_url"] != url_do_documento(e))
            elegivel = url_mudou or velho
        if elegivel:
            elegiveis.append(e)
    # quem tem projeto em andamento primeiro; depois o prazo mais próximo
    elegiveis.sort(
        key=lambda e: (-projetos.get(e["id"], 0), e.get("inscricao_fim") or "9999", e["id"])
    )
    plano.elegiveis = len(elegiveis)
    plano.processar = elegiveis[: max(0, max_por_dia)]
    return plano
