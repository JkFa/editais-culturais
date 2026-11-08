"""Coletor da curadoria manual (`worker/fontes_curadas.yaml`).

O arquivo traz editais conferidos à mão em fonte oficial. A validação aqui é a
barreira de integridade: valor, proponentes, contrapartida e prazo só entram se a
chave correspondente de `evidencias` trouxer o trecho que os comprova.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from ..modelos import LINGUAGENS, PROPONENTES, UFS, EditalBruto, Proveniencia, Radar
from ..padronizacao import (
    fim_de_curadoria,
    inicio_de_curadoria,
    janela_em_meses,
    trecho_comprova_valor,
)

log = logging.getLogger("worker.curadoria")

DIAS_VERIFICACAO_VENCIDA = 45

# Campos derivados cuja evidência é obrigatória quando preenchidos.
_EVIDENCIA_OBRIGATORIA = ("inscricao_fim", "proponentes", "valor_total", "teto_por_projeto",
                          "exige_contrapartida")  # fmt: skip
# Campos derivados que deveriam ter evidência; sem ela, só avisamos no log.
_EVIDENCIA_DESEJAVEL = ("documentos_exigidos",)


class ErroCuradoria(ValueError):
    pass


def _data_iso(v: Any) -> str | None:
    if v in (None, ""):
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


class EditalCurado(BaseModel):
    id_externo: str
    titulo: str
    orgao_fomento: str
    url_origem: str
    url_inscricao: str | None = None
    url_edital_pdf: str | None = None
    resumo_simples: str = Field(max_length=280)
    linguagens: list[str]
    abrangencia: str | None = None
    uf: str | None = None
    municipio: str | None = None
    proponentes: list[str] | None = None
    valor_total: Decimal | None = None
    teto_por_projeto: Decimal | None = None
    num_cotas: int | None = None
    tipo_apoio: str | None = None
    exige_contrapartida: bool | None = None
    marco_legal: str | None = None
    documentos_exigidos: list[str] | None = None
    inscricao_inicio: Any = None
    inscricao_fim: Any
    fim_tem_horario: bool = False
    verificado_em: str
    pendencias: str | None = None
    evidencias: dict[str, str] = Field(default_factory=dict)

    @field_validator("verificado_em", mode="before")
    @classmethod
    def _verificado(cls, v: Any) -> str:
        texto = _data_iso(v)
        if not texto:
            raise ValueError("verificado_em é obrigatório")
        date.fromisoformat(texto[:10])
        return texto[:10]

    @field_validator("linguagens")
    @classmethod
    def _linguagens(cls, v: list[str]) -> list[str]:
        if not v or any(x not in LINGUAGENS for x in v):
            raise ValueError(f"linguagens inválidas: {v}")
        return v

    @field_validator("proponentes")
    @classmethod
    def _proponentes(cls, v: list[str] | None) -> list[str] | None:
        if v and any(x not in PROPONENTES for x in v):
            raise ValueError(f"proponentes inválidos: {v}")
        return v

    @field_validator("uf")
    @classmethod
    def _uf(cls, v: str | None) -> str | None:
        if v is not None and v not in UFS:
            raise ValueError(f"UF inválida: {v}")
        return v

    @field_validator("abrangencia")
    @classmethod
    def _abrangencia(cls, v: str | None) -> str | None:
        if v is not None and v not in ("nacional", "estadual", "municipal"):
            raise ValueError(f"abrangência inválida: {v}")
        return v

    @model_validator(mode="after")
    def _evidencias(self) -> EditalCurado:
        preenchido = {
            "inscricao_fim": self.inscricao_fim,
            "proponentes": self.proponentes,
            "valor_total": self.valor_total,
            "teto_por_projeto": self.teto_por_projeto,
            "exige_contrapartida": self.exige_contrapartida,
        }
        for campo in _EVIDENCIA_OBRIGATORIA:
            if preenchido[campo] not in (None, [], "") and not self.evidencias.get(campo):
                raise ValueError(f"{campo} preenchido sem trecho em evidencias.{campo}")
        for campo in ("valor_total", "teto_por_projeto"):
            valor = getattr(self, campo)
            if valor is not None and not trecho_comprova_valor(valor, self.evidencias[campo]):
                raise ValueError(f"evidencias.{campo} não contém o valor {valor}")
        if self.tipo_apoio == "cessao_de_espaco" and (self.valor_total or self.teto_por_projeto):
            raise ValueError("cessão de espaço não pode ter valor em R$")
        return self

    def para_bruto(self) -> EditalBruto:
        fim, tem_horario = fim_de_curadoria(self.inscricao_fim)
        # `fim_tem_horario` declarado no YAML vence; o parser só infere pelo formato.
        tem_horario = self.fim_tem_horario and tem_horario
        prov = {
            campo: Proveniencia(via="curadoria", trecho=trecho)
            for campo, trecho in self.evidencias.items()
        }
        for campo in _EVIDENCIA_DESEJAVEL:
            valor = getattr(self, campo)
            if valor and campo not in prov:
                log.warning("%s: %s sem evidência registrada", self.id_externo, campo)
                prov[campo] = Proveniencia(via="curadoria", trecho=None)
        return EditalBruto(
            fonte="curadoria",
            id_externo=self.id_externo,
            url_origem=self.url_origem,
            url_inscricao=self.url_inscricao,
            url_edital_pdf=self.url_edital_pdf,
            titulo=self.titulo,
            orgao_fomento=self.orgao_fomento,
            resumo_original=None,
            inscricao_inicio=inicio_de_curadoria(self.inscricao_inicio),
            inscricao_fim=fim,
            fim_tem_horario=tem_horario,
            abrangencia=self.abrangencia,  # type: ignore[arg-type]
            uf=self.uf,
            municipio=self.municipio,
            proveniencia=prov,
            curado=True,
            resumo_simples=self.resumo_simples,
            linguagens=self.linguagens,
            proponentes=self.proponentes or None,
            valor_total=self.valor_total,
            teto_por_projeto=self.teto_por_projeto,
            num_cotas=self.num_cotas,
            exige_contrapartida=self.exige_contrapartida,
            marco_legal=self.marco_legal,
            documentos_exigidos=self.documentos_exigidos or None,
            tipo_apoio=self.tipo_apoio,
            verificado_em=self.verificado_em,
            pendencias=self.pendencias,
            verificacao="curadoria",
        )


class RadarCurado(BaseModel):
    id_externo: str
    titulo: str
    orgao_fomento: str
    url_referencia: str | None = None
    ultima_edicao: str | None = None
    janela_habitual: str | None = None
    proponentes_ultima_edicao: list[str] | None = None
    linguagens: list[str] = Field(default_factory=list)
    verificado_em: str

    @field_validator("verificado_em", mode="before")
    @classmethod
    def _verificado(cls, v: Any) -> str:
        texto = _data_iso(v)
        if not texto:
            raise ValueError("verificado_em é obrigatório")
        date.fromisoformat(texto[:10])
        return texto[:10]

    def para_radar(self) -> Radar:
        janela = janela_em_meses(self.janela_habitual)
        return Radar(
            **self.model_dump(),
            janela_mes_inicio=janela[0] if janela else None,
            janela_mes_fim=janela[1] if janela else None,
        )


class ResultadoCuradoria(BaseModel):
    editais: list[EditalBruto]
    radar: list[Radar]
    avisos: list[str] = Field(default_factory=list)


def avisos_de_vencimento(
    entradas: list[tuple[str, str]], hoje: date, limite_dias: int = DIAS_VERIFICACAO_VENCIDA
) -> list[str]:
    avisos = []
    for id_externo, verificado_em in entradas:
        dias = (hoje - date.fromisoformat(verificado_em)).days
        if dias > limite_dias:
            avisos.append(
                f"curadoria: '{id_externo}' verificado há {dias} dias (limite {limite_dias}); "
                "reconfira a fonte"
            )
    return avisos


def carregar_curadoria(caminho: Path, hoje: date | None = None) -> ResultadoCuradoria:
    """Lê e valida o YAML. Uma entrada inválida derruba a fonte inteira (erro explícito)."""
    hoje = hoje or datetime.now(UTC).date()
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    editais: list[EditalBruto] = []
    verificacoes: list[tuple[str, str]] = []
    ids: set[str] = set()
    for i, entrada in enumerate(dados.get("editais") or []):
        try:
            curado = EditalCurado.model_validate(entrada)
        except ValueError as exc:
            ident = entrada.get("id_externo", f"#{i}") if isinstance(entrada, dict) else f"#{i}"
            raise ErroCuradoria(f"edital '{ident}' inválido em {caminho.name}: {exc}") from exc
        if curado.id_externo in ids:
            raise ErroCuradoria(f"id_externo duplicado na curadoria: {curado.id_externo}")
        ids.add(curado.id_externo)
        editais.append(curado.para_bruto())
        verificacoes.append((curado.id_externo, curado.verificado_em))
        if curado.pendencias:
            log.warning("curadoria: %s tem pendência: %s", curado.id_externo, curado.pendencias)
    radar: list[Radar] = []
    for i, entrada in enumerate(dados.get("radar") or []):
        try:
            item = RadarCurado.model_validate(entrada)
        except ValueError as exc:
            ident = entrada.get("id_externo", f"#{i}") if isinstance(entrada, dict) else f"#{i}"
            raise ErroCuradoria(f"radar '{ident}' inválido em {caminho.name}: {exc}") from exc
        if item.id_externo in ids:
            raise ErroCuradoria(f"id_externo duplicado entre editais e radar: {item.id_externo}")
        ids.add(item.id_externo)
        radar.append(item.para_radar())
        verificacoes.append((item.id_externo, item.verificado_em))
    return ResultadoCuradoria(
        editais=editais, radar=radar, avisos=avisos_de_vencimento(verificacoes, hoje)
    )
