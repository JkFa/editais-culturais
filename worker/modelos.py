"""Modelos de dados do worker.

`EditalBruto` é o que os coletores entregam (já com datas em UTC). `Edital` é a linha
final, espelhando `supabase/schema.sql`. Todo campo derivado é anulável e carrega a
proveniência em `proveniencia[campo]`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

LINGUAGENS = (
    "Música",
    "Cinema & Audiovisual",
    "Teatro & Dança",
    "Fotografia",
    "Artes Visuais",
    "Literatura",
    "Patrimônio",
    "Multiáreas",
)
PROPONENTES = ("PF", "MEI", "PJ_COM_FINS", "PJ_SEM_FINS", "COLETIVO")
UFS = "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()

Via = Literal["regex", "llm", "curadoria", "api"]
Abrangencia = Literal["nacional", "estadual", "municipal"]

LIMITE_RESUMO_ORIGINAL = 2000
LIMITE_RESUMO_SIMPLES = 280


class Proveniencia(BaseModel):
    via: Via
    trecho: str | None = None


class EditalBruto(BaseModel):
    """Saída de um coletor. Campos `None` significam "a fonte não informa"."""

    fonte: str
    id_externo: str
    url_origem: str
    url_inscricao: str | None = None
    url_edital_pdf: str | None = None
    titulo: str
    orgao_fomento: str
    resumo_original: str | None = None
    areas: list[str] = Field(default_factory=list)
    inscricao_inicio: datetime | None = None
    inscricao_fim: datetime
    fim_tem_horario: bool = False
    publicado_em: datetime | None = None
    abrangencia: Abrangencia | None = None
    uf: str | None = None
    municipio: str | None = None
    proveniencia: dict[str, Proveniencia] = Field(default_factory=dict)

    # Preenchidos só pela curadoria, que já traz tudo conferido à mão.
    curado: bool = False
    resumo_simples: str | None = None
    linguagens: list[str] | None = None
    proponentes: list[str] | None = None
    valor_total: Decimal | None = None
    teto_por_projeto: Decimal | None = None
    num_cotas: int | None = None
    exige_contrapartida: bool | None = None
    marco_legal: str | None = None
    documentos_exigidos: list[str] | None = None
    cronograma: list[dict[str, str]] | None = None
    tipo_apoio: str | None = None
    verificado_em: str | None = None
    pendencias: str | None = None

    @field_validator("inscricao_inicio", "inscricao_fim", "publicado_em")
    @classmethod
    def _exige_fuso(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime sem fuso; converta para UTC no coletor")
        return v.astimezone(UTC) if v is not None else None


class Edital(BaseModel):
    fonte: str
    id_externo: str
    url_origem: str
    url_inscricao: str | None = None
    url_edital_pdf: str | None = None
    titulo: str
    orgao_fomento: str
    resumo_original: str | None = None
    resumo_simples: str | None = None
    linguagens: list[str] = Field(default_factory=list)
    abrangencia: Abrangencia | None = None
    uf: str | None = None
    municipio: str | None = None
    proponentes: list[str] | None = None
    valor_total: Decimal | None = None
    teto_por_projeto: Decimal | None = None
    num_cotas: int | None = None
    exige_contrapartida: bool | None = None
    marco_legal: str | None = None
    documentos_exigidos: list[str] | None = None
    cronograma: list[dict[str, str]] | None = None
    tipo_apoio: str | None = None
    verificado_em: str | None = None
    pendencias: str | None = None
    inscricao_inicio: datetime | None = None
    inscricao_fim: datetime
    fim_tem_horario: bool = False
    publicado_em: datetime | None = None
    coletado_em: datetime | None = None
    atualizado_em: datetime | None = None
    hash_conteudo: str | None = None
    proveniencia: dict[str, Proveniencia] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valida(self) -> Edital:
        invalidas = [x for x in self.linguagens if x not in LINGUAGENS]
        if invalidas:
            raise ValueError(f"linguagens fora da taxonomia: {invalidas}")
        invalidos = [x for x in (self.proponentes or []) if x not in PROPONENTES]
        if invalidos:
            raise ValueError(f"proponentes inválidos: {invalidos}")
        if self.uf is not None and self.uf not in UFS:
            raise ValueError(f"UF inválida: {self.uf}")
        if self.abrangencia == "nacional" and self.uf is not None:
            raise ValueError("edital nacional não pode ter UF")
        for campo in ("valor_total", "teto_por_projeto"):
            valor = getattr(self, campo)
            if valor is not None and valor < 0:
                raise ValueError(f"{campo} negativo")
        if self.resumo_simples and len(self.resumo_simples) > LIMITE_RESUMO_SIMPLES:
            raise ValueError("resumo_simples acima de 280 caracteres")
        if self.resumo_original and len(self.resumo_original) > LIMITE_RESUMO_ORIGINAL:
            raise ValueError("resumo_original acima de 2000 caracteres")
        return self

    @property
    def chave(self) -> str:
        """Identificador estável usado em `?edital=` (igual no Supabase e no snapshot)."""
        return f"{self.fonte}:{self.id_externo}"

    def para_linha(self) -> dict[str, Any]:
        """Dicionário JSON-serializável com exatamente as colunas de `public.editais`."""
        dados = self.model_dump(mode="json")
        dados["valor_total"] = _num(self.valor_total)
        dados["teto_por_projeto"] = _num(self.teto_por_projeto)
        return dados

    def calcular_hash(self) -> str:
        """Hash do conteúdo. Exclui carimbos de tempo para não gerar update inútil."""
        dados = self.para_linha()
        for campo in ("coletado_em", "atualizado_em", "hash_conteudo"):
            dados.pop(campo, None)
        bruto = json.dumps(dados, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(bruto.encode()).hexdigest()


class Radar(BaseModel):
    id_externo: str
    titulo: str
    orgao_fomento: str
    url_referencia: str | None = None
    ultima_edicao: str | None = None
    janela_habitual: str | None = None
    janela_mes_inicio: int | None = None
    janela_mes_fim: int | None = None
    proponentes_ultima_edicao: list[str] | None = None
    linguagens: list[str] = Field(default_factory=list)
    verificado_em: str | None = None

    @model_validator(mode="after")
    def _valida(self) -> Radar:
        if any(x not in LINGUAGENS for x in self.linguagens):
            raise ValueError("linguagens fora da taxonomia no radar")
        if any(x not in PROPONENTES for x in (self.proponentes_ultima_edicao or [])):
            raise ValueError("proponentes inválidos no radar")
        return self


def _num(valor: Decimal | None) -> float | None:
    return None if valor is None else float(valor)
