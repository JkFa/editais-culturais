"""Catálogo de fontes (config + saúde) e `fontes_status`."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
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


class Territorio(BaseModel):
    """De que território é a fonte (usado só pelo placar de cobertura)."""

    ente: Literal["estado", "capital", "municipio"]
    uf: str
    municipio: str | None = None
    parcial: bool = False


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
    cobertura: Territorio | None = None

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


# Falha ao CONECTAR (nem chegou a haver resposta HTTP). Pode ser instabilidade do site ou filtro de IP
# contra os servidores do GitHub; só a persistência distingue. Não contornamos: vira `bloqueada_em_ci`.
FALHAS_DE_CONEXAO = (
    "ConnectTimeout",
)  # a assinatura observada em PE, SE e Campinas; ConnectError/SSL é problema do site
DIAS_PARA_FALHA_PERSISTENTE = 3
_DESDE = re.compile(r"\[desde (\d{4}-\d{2}-\d{2})\]")


def eh_falha_de_conexao(erro: str | None) -> bool:
    return bool(erro) and any(t in erro for t in FALHAS_DE_CONEXAO)


def classificar_falha(
    erro: str | None, anterior: dict[str, Any] | None, agora: datetime
) -> tuple[StatusValor, str | None]:
    """(status, texto do erro) de uma fonte que falhou na coleta de hoje.

    - 403/429/desafio anti-bot: `bloqueada_em_ci` na hora (o site recusou o robô).
    - Falha de conexão: `erro` até persistir por `DIAS_PARA_FALHA_PERSISTENTE` dias seguidos; depois `bloqueada_em_ci`
      (entra na Issue semanal de conferência manual). O início da sequência vai no texto: `[desde AAAA-MM-DD]`.
    - Qualquer outro erro (parser, HTTP 5xx, JSON inesperado): `erro`.
    """
    erro = erro or ""
    if "403" in erro or "429" in erro:
        return "bloqueada_em_ci", erro or None
    if not eh_falha_de_conexao(erro):
        return "erro", erro or None
    anterior = anterior or {}
    desde = agora.date()
    if anterior.get("status") in ("erro", "bloqueada_em_ci") and eh_falha_de_conexao(
        anterior.get("erro")
    ):
        achou = _DESDE.search(anterior.get("erro") or "")
        if achou:
            desde = date.fromisoformat(achou.group(1))
        else:  # registro antigo, sem a marca: a última execução é o melhor palpite do início
            ultima = _dt(anterior.get("ultima_execucao"))
            desde = ultima.date() if ultima else desde
    base = _DESDE.sub("", erro).strip()
    texto = f"{base} [desde {desde.isoformat()}]"
    persistente = (agora.date() - desde).days >= DIAS_PARA_FALHA_PERSISTENTE
    return ("bloqueada_em_ci" if persistente else "erro"), texto


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
