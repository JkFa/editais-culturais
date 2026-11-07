"""Catálogo de fontes (config + saúde) e `fontes_status`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from .modelos import TIPOS_FONTE

Metodo = Literal["api", "html", "rss", "playwright", "vigia"]
StatusValor = Literal[
    "ok",
    "erro",
    "bloqueada_em_ci",
    "desativada",
    "sem_dados",
    "conteudo_insuficiente",
    "bloqueada_por_robots",
]


class FonteConfig(BaseModel):
    id: str
    nome: str
    tipo: str
    metodo: Metodo
    url: str
    coletor: str | None = None
    termos_url: str | None = None
    termos_conferidos_em: str | None = None
    robots_ok: bool | None = None
    intervalo_min_entre_requisicoes_s: float = 2.0
    ativa: bool = True
    motivo: str | None = None
    ignorar_hosts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valida(self) -> FonteConfig:
        if self.tipo not in TIPOS_FONTE:
            raise ValueError(f"tipo de fonte inválido: {self.tipo}")
        if not self.ativa and not self.motivo:
            raise ValueError(f"fonte {self.id} desativada sem motivo")
        return self


class StatusFonte(BaseModel):
    fonte: str
    nome: str
    tipo: str
    metodo: str
    url: str
    ativa: bool
    status: StatusValor
    erro: str | None = None
    motivo: str | None = None
    ultima_execucao: datetime | None = None
    ultimo_sucesso: datetime | None = None
    total_coletado: int = 0
    candidatos_criados: int = 0
    duracao_ms: int = 0
    termos_url: str | None = None
    termos_conferidos_em: str | None = None


def carregar_fontes_v4(caminho: Path) -> list[FonteConfig]:
    dados: dict[str, Any] = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    return [FonteConfig.model_validate(f) for f in dados.get("fontes", [])]


def status_desativada(f: FonteConfig) -> StatusFonte:
    return StatusFonte(
        fonte=f.id, nome=f.nome, tipo=f.tipo, metodo=f.metodo, url=f.url, ativa=False,
        status="desativada", motivo=f.motivo, termos_url=f.termos_url,
        termos_conferidos_em=f.termos_conferidos_em,
    )  # fmt: skip


def status_de(
    f: FonteConfig,
    status: StatusValor,
    anterior: dict[str, Any] | None,
    *,
    erro: str | None = None,
    total: int = 0,
    candidatos: int = 0,
    duracao_ms: int = 0,
    agora: datetime | None = None,
) -> StatusFonte:
    agora = agora or datetime.now(UTC)
    anterior = anterior or {}
    sucesso = agora if status in ("ok", "sem_dados") else _dt(anterior.get("ultimo_sucesso"))
    return StatusFonte(
        fonte=f.id, nome=f.nome, tipo=f.tipo, metodo=f.metodo, url=f.url, ativa=True, status=status,
        erro=erro, ultima_execucao=agora, ultimo_sucesso=sucesso, total_coletado=total,
        candidatos_criados=candidatos, duracao_ms=duracao_ms, termos_url=f.termos_url,
        termos_conferidos_em=f.termos_conferidos_em,
    )  # fmt: skip


def _dt(valor: Any) -> datetime | None:
    return datetime.fromisoformat(str(valor).replace("Z", "+00:00")) if valor else None
