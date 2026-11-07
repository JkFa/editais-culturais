"""Tipos compartilhados pelos coletores da v4."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..fila import Candidato
from ..modelos import EditalBruto


@dataclass
class ResultadoFonte:
    fonte: str
    brutos: list[EditalBruto] = field(default_factory=list)
    textos: dict[str, str] = field(default_factory=dict)  # id_externo -> texto completo (regex/LLM)
    candidatos: list[Candidato] = field(default_factory=list)
    descartes: Counter[str] = field(default_factory=Counter)
    status: str = "ok"  # ok | sem_dados | conteudo_insuficiente
    detalhe: str | None = None
    estado: dict[str, object] = field(default_factory=dict)  # cache a persistir (vigias)
