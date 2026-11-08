"""O que o modelo devolve (esquema estruturado) e o que sobra depois da validação."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

SECOES = [
    "ficha", "apresentacao", "justificativa", "objetivos", "metas", "publico",
    "acessibilidade", "democratizacao", "comunicacao", "equipe", "cronograma", "orcamento",
]  # fmt: skip
ETAPAS = [
    "pre_producao",
    "producao",
    "pos_producao",
    "divulgacao",
    "acessibilidade",
    "administrativo",
]
MAX_TRECHO = 200


# ── esquema enviado ao Gemini (subconjunto de JSON Schema; tipos em maiúsculas) ───────────────


def _escalar(tipo: str) -> dict[str, Any]:
    return {
        "type": "OBJECT",
        "nullable": True,
        "properties": {
            "valor": {"type": tipo},
            "trecho": {"type": "STRING"},
            "pagina": {"type": "INTEGER", "nullable": True},
        },
        "required": ["valor", "trecho"],
    }


def _item(extra: dict[str, Any], obrigatorios: list[str]) -> dict[str, Any]:
    return {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                **extra,
                "trecho": {"type": "STRING"},
                "pagina": {"type": "INTEGER", "nullable": True},
            },
            "required": [*obrigatorios, "trecho"],
        },
    }


SCHEMA_GEMINI: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "criterios": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "nome": {"type": "STRING"},
                    "descricao": {"type": "STRING", "nullable": True},
                    "peso": {"type": "NUMBER", "nullable": True},
                    "nota_max": {"type": "NUMBER", "nullable": True},
                    "trecho_edital": {"type": "STRING"},
                    "pagina": {"type": "INTEGER", "nullable": True},
                },
                "required": ["nome", "trecho_edital"],
            },
        },
        "teto_orcamento": _escalar("NUMBER"),
        "teto_por_proponente": _escalar("NUMBER"),
        "percentuais_max": _item(
            {"etapa": {"type": "STRING", "enum": ETAPAS}, "percentual": {"type": "NUMBER"}},
            ["etapa", "percentual"],
        ),
        "percentuais_min": _item(
            {"etapa": {"type": "STRING", "enum": ETAPAS}, "percentual": {"type": "NUMBER"}},
            ["etapa", "percentual"],
        ),
        "rubricas_vedadas": _item({"rubrica": {"type": "STRING"}}, ["rubrica"]),
        "limites_caracteres": _item(
            {"secao": {"type": "STRING", "enum": SECOES}, "limite": {"type": "INTEGER"}},
            ["secao", "limite"],
        ),
        "campos_exigidos": _item(
            {
                "nome": {"type": "STRING"},
                "secao": {"type": "STRING", "enum": SECOES, "nullable": True},
            },
            ["nome"],
        ),
        "contrapartida_exigida": _escalar("STRING"),
        "prazo_execucao_meses": _escalar("INTEGER"),
        "exige_acessibilidade": _escalar("BOOLEAN"),
        "observacoes": {"type": "STRING", "nullable": True},
    },
}


# ── validação da resposta (pydantic): tipo errado = resposta inválida = erro da extração ──────


class _Base(BaseModel):
    model_config = {"extra": "ignore"}


class Escalar(_Base):
    valor: Any
    trecho: str
    pagina: int | None = None


class CriterioLLM(_Base):
    nome: str
    descricao: str | None = None
    peso: float | None = None
    nota_max: float | None = None
    trecho_edital: str
    pagina: int | None = None


class PercentualLLM(_Base):
    etapa: str
    percentual: float
    trecho: str
    pagina: int | None = None


class RubricaLLM(_Base):
    rubrica: str
    trecho: str
    pagina: int | None = None


class LimiteLLM(_Base):
    secao: str
    limite: int
    trecho: str
    pagina: int | None = None


class CampoLLM(_Base):
    nome: str
    secao: str | None = None
    trecho: str
    pagina: int | None = None


class RespostaLLM(_Base):
    criterios: list[CriterioLLM] = Field(default_factory=list)
    teto_orcamento: Escalar | None = None
    teto_por_proponente: Escalar | None = None
    percentuais_max: list[PercentualLLM] = Field(default_factory=list)
    percentuais_min: list[PercentualLLM] = Field(default_factory=list)
    rubricas_vedadas: list[RubricaLLM] = Field(default_factory=list)
    limites_caracteres: list[LimiteLLM] = Field(default_factory=list)
    campos_exigidos: list[CampoLLM] = Field(default_factory=list)
    contrapartida_exigida: Escalar | None = None
    prazo_execucao_meses: Escalar | None = None
    exige_acessibilidade: Escalar | None = None
    observacoes: str | None = None
