"""Curadoria manual de oportunidades internacionais (`worker/curadoria_internacional.json`).

O schema (`curadoria_internacional.schema.json`) é validado no CI. Aqui repetimos as regras que
importam em produção com pydantic (o worker não carrega `jsonschema`): prazo futuro, `aberto`
e custos só com o trecho da fonte, sem URL de exemplo. Uma entrada inválida derruba a fonte
inteira, com erro explícito (igual à curadoria dos editais brasileiros).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ..modelos import (
    CUSTOS_COBERTOS,
    LINGUAGENS,
    EditalBruto,
    ElegibilidadeBr,
    Proveniencia,
    TipoOportunidade,
)
from ..padronizacao import FUSO_PADRAO, fim_de_curadoria

log = logging.getLogger("worker.curadoria_internacional")

FONTE = "curadoria-internacional"
ROTULO_FONTE = "curadoria do portal"
DIAS_CONFERENCIA_VENCIDA = 45
_HOSTS_DE_EXEMPLO = re.compile(
    r"(^|\.)(example\.(com|org|net)|exemplo\.\w+|invalid|test|localhost)$"
)


class ErroCuradoriaInternacional(ValueError):
    pass


class Oportunidade(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,80}$")
    titulo_original: str = Field(min_length=5, max_length=300)
    organizacao: str = Field(min_length=2, max_length=200)
    url_oficial: str = Field(pattern=r"^https://\S+$")
    url_inscricao: str | None = Field(default=None, pattern=r"^https://\S+$")
    url_bases_pdf: str | None = Field(default=None, pattern=r"^https://\S+$")
    tipo_oportunidade: TipoOportunidade
    pais_destino: str | None = None
    elegibilidade_br: ElegibilidadeBr
    custos_cobertos: list[str] | None = None
    idioma_original: str = Field(pattern=r"^[a-z]{2}$")
    prazo_inicio: str | None = None
    prazo_original: str
    prazo_fuso: str | None = None
    moeda: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    valor_original: Decimal | None = Field(default=None, ge=0)
    linguagens: list[str] = Field(min_length=1)
    observacao_curta: str = Field(min_length=20, max_length=280)
    conferido_em: date
    evidencias: dict[str, str]

    @model_validator(mode="after")
    def _valida(self) -> Oportunidade:
        for url in (self.url_oficial, self.url_inscricao, self.url_bases_pdf):
            host = (url or "https://x.org").split("/")[2].lower()
            if _HOSTS_DE_EXEMPLO.search(host):
                raise ValueError(f"URL de exemplo não pode ser publicada: {url}")
        if any(x not in LINGUAGENS for x in self.linguagens):
            raise ValueError(f"linguagens fora da taxonomia: {self.linguagens}")
        if any(x not in CUSTOS_COBERTOS for x in (self.custos_cobertos or [])):
            raise ValueError(f"custos_cobertos inválidos: {self.custos_cobertos}")
        if "prazo_original" not in self.evidencias:
            raise ValueError("falta evidencias.prazo_original (trecho da página oficial)")
        if self.elegibilidade_br != "verificar" and "elegibilidade_br" not in self.evidencias:
            raise ValueError(
                "elegibilidade diferente de 'verificar' exige evidencias.elegibilidade_br"
            )
        if self.custos_cobertos and "custos_cobertos" not in self.evidencias:
            raise ValueError("custos_cobertos exige evidencias.custos_cobertos")
        return self

    def prazo(self) -> tuple[datetime, bool]:
        """(fim em UTC, tem horário). Só data: vale até 23:59:59 em Brasília (padrão do portal)."""
        if len(self.prazo_original) == 10:
            return fim_de_curadoria(self.prazo_original)
        dt = datetime.fromisoformat(self.prazo_original.replace("Z", "+00:00"))
        return dt.astimezone(UTC), True

    def para_bruto(self) -> EditalBruto:
        fim, tem_horario = self.prazo()
        inicio = None
        if self.prazo_inicio:
            inicio = (
                datetime.fromisoformat(self.prazo_inicio[:10])
                .replace(tzinfo=FUSO_PADRAO)
                .astimezone(UTC)
            )
        prov = {
            campo: Proveniencia(via="curadoria", trecho=trecho)
            for campo, trecho in self.evidencias.items()
        }
        prov["inscricao_fim"] = prov.pop("prazo_original")
        original = (
            datetime.fromisoformat(self.prazo_original.replace("Z", "+00:00"))
            if len(self.prazo_original) > 10
            else None
        )
        return EditalBruto(
            fonte=FONTE,
            id_externo=self.id,
            url_origem=self.url_oficial,
            url_inscricao=self.url_inscricao,
            url_edital_pdf=self.url_bases_pdf,
            titulo=self.titulo_original,
            orgao_fomento=self.organizacao,
            resumo_original=None,
            inscricao_inicio=inicio,
            inscricao_fim=fim,
            fim_tem_horario=tem_horario,
            via=ROTULO_FONTE,
            curado=True,
            resumo_simples=self.observacao_curta,
            linguagens=self.linguagens,
            verificado_em=self.conferido_em.isoformat(),
            proveniencia=prov,
            ambito="internacional",
            pais_destino=self.pais_destino,
            elegibilidade_br=self.elegibilidade_br,
            tipo_oportunidade=self.tipo_oportunidade,
            custos_cobertos=self.custos_cobertos or None,
            idioma_original=self.idioma_original,
            moeda=self.moeda,
            valor_original=self.valor_original,
            prazo_original=original,
            prazo_fuso=self.prazo_fuso,
            titulo_original=self.titulo_original,
        )


class ResultadoCuradoriaInternacional(BaseModel):
    editais: list[EditalBruto] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)


def carregar(caminho: Path, hoje: date | None = None) -> ResultadoCuradoriaInternacional:
    hoje = hoje or datetime.now(UTC).date()
    try:
        dados: dict[str, Any] = json.loads(caminho.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ErroCuradoriaInternacional(f"JSON inválido em {caminho.name}: {exc}") from exc
    saida = ResultadoCuradoriaInternacional()
    ids: set[str] = set()
    for i, entrada in enumerate(dados.get("oportunidades") or []):
        ident = entrada.get("id", f"#{i}") if isinstance(entrada, dict) else f"#{i}"
        try:
            op = Oportunidade.model_validate(entrada)
        except ValueError as exc:
            raise ErroCuradoriaInternacional(
                f"'{ident}' inválida em {caminho.name}: {exc}"
            ) from exc
        if op.id in ids:
            raise ErroCuradoriaInternacional(f"id duplicado na curadoria internacional: {op.id}")
        ids.add(op.id)
        fim, _ = op.prazo()
        if fim.date() < hoje:
            saida.avisos.append(f"{op.id}: prazo vencido ({op.prazo_original}); não publicada")
            continue
        if (hoje - op.conferido_em).days > DIAS_CONFERENCIA_VENCIDA:
            saida.avisos.append(
                f"{op.id}: conferida há mais de {DIAS_CONFERENCIA_VENCIDA} dias; reconfira a página oficial"
            )
        saida.editais.append(op.para_bruto())
    return saida
