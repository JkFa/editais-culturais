"""Da página do edital ao registro de critérios: divide, consulta o modelo, funde, valida e VERIFICA.

Regra de ouro: nada vindo do modelo é gravado sem um trecho que exista, de verdade, no texto do edital
(busca normalizada). Número sem o próprio número no trecho também cai. O que não passa vira `null` (ou
sai da lista) e o motivo vai para o log; resposta com a forma errada derruba a extração (status `erro`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .documento import Documento
from .esquema import ETAPAS, MAX_TRECHO, SCHEMA_GEMINI, SECOES, Escalar, RespostaLLM
from .llm import Provedor
from .prompt import montar_prompt
from .texto import normalizar, slug, trecho_comprova_numero, trecho_existe

log = logging.getLogger("extrair_criterios")

LIMITE_PARTE = 22_000  # caracteres por chamada
MAX_PARTES = 8  # documento maior que isso é lido só até aqui (e o log avisa)


class ErroExtracao(Exception):
    """A extração falhou (resposta inválida, por exemplo). Vira status `erro`; tenta de novo outro dia."""


@dataclass
class ResultadoExtracao:
    campos: dict[str, Any]
    descartes: list[str] = field(default_factory=list)
    partes: int = 1
    truncado: bool = False

    @property
    def vazio(self) -> bool:
        c = self.campos
        return not (
            c["criterios"]
            or c["percentuais_max"]
            or c["percentuais_min"]
            or c["rubricas_vedadas"]
            or c["limites_caracteres"]
            or c["campos_exigidos"]
            or any(
                c[k] is not None
                for k in (
                    "teto_orcamento",
                    "teto_por_proponente",
                    "contrapartida_exigida",
                    "prazo_execucao_meses",
                    "exige_acessibilidade",
                )
            )
        )


# ── divisão em partes ────────────────────────────────────────────────────────────────────────


def dividir(doc: Documento, limite: int = LIMITE_PARTE) -> list[list[tuple[int, str]]]:
    """Agrupa páginas em partes de até `limite` caracteres. Página maior que o limite é cortada em pedaços."""
    unidades: list[tuple[int, str]] = []
    for n, texto in enumerate(doc.paginas, start=1):
        texto = texto.strip()
        if not texto:
            continue
        for i in range(0, len(texto), limite):
            unidades.append((n, texto[i : i + limite]))
    partes: list[list[tuple[int, str]]] = []
    atual: list[tuple[int, str]] = []
    tamanho = 0
    for u in unidades:
        if atual and tamanho + len(u[1]) > limite:
            partes.append(atual)
            atual, tamanho = [], 0
        atual.append(u)
        tamanho += len(u[1])
    if atual:
        partes.append(atual)
    return partes


# ── fusão das respostas das partes ───────────────────────────────────────────────────────────


def fundir(respostas: list[RespostaLLM]) -> RespostaLLM:
    saida = RespostaLLM()
    vistos: dict[str, set[str]] = {
        k: set()
        for k in (
            "criterios",
            "percentuais_max",
            "percentuais_min",
            "rubricas_vedadas",
            "limites_caracteres",
            "campos_exigidos",
        )
    }
    chaves = {
        "criterios": lambda x: normalizar(x.nome),
        "percentuais_max": lambda x: x.etapa,
        "percentuais_min": lambda x: x.etapa,
        "rubricas_vedadas": lambda x: normalizar(x.rubrica),
        "limites_caracteres": lambda x: x.secao,
        "campos_exigidos": lambda x: normalizar(x.nome),
    }
    for r in respostas:
        for lista, chave in chaves.items():
            for item in getattr(r, lista):
                k = chave(item)
                if k and k not in vistos[lista]:
                    vistos[lista].add(k)
                    getattr(saida, lista).append(item)
        for esc in (
            "teto_orcamento",
            "teto_por_proponente",
            "contrapartida_exigida",
            "prazo_execucao_meses",
            "exige_acessibilidade",
        ):
            if getattr(saida, esc) is None and getattr(r, esc) is not None:
                setattr(saida, esc, getattr(r, esc))
        if r.observacoes and not saida.observacoes:
            saida.observacoes = r.observacoes
    return saida


# ── validação e verificação ──────────────────────────────────────────────────────────────────

_POR_EXTENSO = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
    "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14, "quinze": 15, "dezoito": 18,
    "vinte": 20, "vinte e quatro": 24, "trinta e seis": 36,
}  # fmt: skip


def _prazo_no_trecho(meses: int, trecho: str) -> bool:
    if trecho_comprova_numero(meses, trecho):
        return True
    t = normalizar(trecho)
    return any(
        v == meses and re.search(rf"\b{re.escape(palavra)}\b", t)
        for palavra, v in _POR_EXTENSO.items()
    )


def validar_e_verificar(r: RespostaLLM, doc: Documento) -> ResultadoExtracao:
    texto_n = normalizar(doc.texto)
    n_paginas = len(doc.paginas) if doc.tipo == "pdf" else None
    descartes: list[str] = []
    evidencias: dict[str, dict[str, Any]] = {}

    def prova(rotulo: str, trecho: str, pagina: int | None) -> tuple[bool, str, int | None]:
        if not trecho_existe(trecho, texto_n):
            descartes.append(f"{rotulo}: trecho não encontrado no documento ({trecho[:60]!r})")
            return False, "", None
        pg = pagina if n_paginas and isinstance(pagina, int) and 1 <= pagina <= n_paginas else None
        return True, trecho.strip()[:MAX_TRECHO], pg

    def escalar(nome: str, esc: Escalar | None, tipo: type, checar=None) -> Any:
        if esc is None:
            return None
        v = esc.valor
        if tipo is bool:
            ok = isinstance(v, bool)
        elif tipo is str:
            ok = isinstance(v, str) and 3 <= len(v.strip()) <= 600
        elif tipo is int:
            ok = (
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and float(v).is_integer()
                and 1 <= v <= 120
            )
        else:
            ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
        if not ok:
            descartes.append(f"{nome}: valor inválido ({v!r})")
            return None
        v = (
            v.strip()
            if tipo is str
            else int(v)
            if tipo is int
            else float(v)
            if tipo is float
            else v
        )
        achou, trecho, pg = prova(nome, esc.trecho, esc.pagina)
        if not achou:
            return None
        if checar and not checar(v, trecho):
            descartes.append(f"{nome}: o número {v} não aparece no trecho citado")
            return None
        evidencias[nome] = {"trecho": trecho, "pagina": pg}
        return v

    teto = escalar(
        "teto_orcamento", r.teto_orcamento, float, lambda v, t: trecho_comprova_numero(v, t)
    )
    teto_prop = escalar(
        "teto_por_proponente",
        r.teto_por_proponente,
        float,
        lambda v, t: trecho_comprova_numero(v, t),
    )
    prazo = escalar("prazo_execucao_meses", r.prazo_execucao_meses, int, _prazo_no_trecho)
    acess = escalar("exige_acessibilidade", r.exige_acessibilidade, bool)
    contra = escalar("contrapartida_exigida", r.contrapartida_exigida, str)

    def percentuais(nome: str, itens: list) -> dict[str, float]:
        saida: dict[str, float] = {}
        for p in itens:
            rot = f"{nome}.{p.etapa}"
            if p.etapa not in ETAPAS or not (0 < p.percentual <= 1):
                descartes.append(
                    f"{rot}: percentual inválido ({p.percentual!r}); use fração entre 0 e 1"
                )
                continue
            achou, trecho, pg = prova(rot, p.trecho, p.pagina)
            if not achou:
                continue
            if not trecho_comprova_numero(p.percentual, trecho, percentual=True):
                descartes.append(f"{rot}: o percentual {p.percentual} não aparece no trecho citado")
                continue
            saida[p.etapa] = round(p.percentual, 4)
            evidencias[rot] = {"trecho": trecho, "pagina": pg}
        return saida

    pmax = percentuais("percentuais_max", r.percentuais_max)
    pmin = percentuais("percentuais_min", r.percentuais_min)

    rubricas: list[str] = []
    for x in r.rubricas_vedadas:
        nome = x.rubrica.strip()
        if not 3 <= len(nome) <= 120:
            descartes.append(f"rubricas_vedadas: nome inválido ({nome[:40]!r})")
            continue
        achou, trecho, pg = prova(f"rubricas_vedadas.{nome[:30]}", x.trecho, x.pagina)
        if achou:
            rubricas.append(nome)
            evidencias[f"rubricas_vedadas.{slug(nome)}"] = {"trecho": trecho, "pagina": pg}

    limites: dict[str, int] = {}
    for x in r.limites_caracteres:
        rot = f"limites_caracteres.{x.secao}"
        if x.secao not in SECOES or not 100 <= x.limite <= 20000:
            descartes.append(f"{rot}: limite inválido ({x.limite!r})")
            continue
        achou, trecho, pg = prova(rot, x.trecho, x.pagina)
        if not achou:
            continue
        if not trecho_comprova_numero(x.limite, trecho):
            descartes.append(f"{rot}: o número {x.limite} não aparece no trecho citado")
            continue
        limites[x.secao] = x.limite
        evidencias[rot] = {"trecho": trecho, "pagina": pg}

    campos: list[dict[str, Any]] = []
    for x in r.campos_exigidos:
        nome = x.nome.strip()
        if not 2 <= len(nome) <= 200 or (x.secao is not None and x.secao not in SECOES):
            descartes.append(f"campos_exigidos: item inválido ({nome[:40]!r}, {x.secao!r})")
            continue
        achou, trecho, pg = prova(f"campos_exigidos.{nome[:30]}", x.trecho, x.pagina)
        if achou:
            evidencias[f"campos_exigidos.{len(campos)}"] = {"trecho": trecho, "pagina": pg}
            campos.append({"nome": nome, "secao": x.secao})

    criterios: list[dict[str, Any]] = []
    usados: set[str] = set()
    for x in r.criterios:
        nome = x.nome.strip()
        if not 3 <= len(nome) <= 200:
            descartes.append(f"criterio: nome inválido ({nome[:40]!r})")
            continue
        achou, trecho, pg = prova(f"criterio {nome[:40]}", x.trecho_edital, x.pagina)
        if not achou:
            continue
        base = slug(nome)
        id_ = base
        i = 2
        while id_ in usados:
            id_, i = f"{base}_{i}", i + 1
        usados.add(id_)
        peso = x.peso if x.peso and x.peso > 0 else None
        nota = x.nota_max if x.nota_max and x.nota_max > 0 else None
        criterios.append({
            "id": id_, "nome": nome, "descricao": (x.descricao or "").strip()[:400] or None,
            "peso": peso, "nota_max": nota, "trecho_edital": trecho, "pagina": pg,
        })  # fmt: skip
    # soma dos pesos coerente: pesos em fração precisam somar 1; senão não confiamos neles
    pesos = [c["peso"] for c in criterios if c["peso"] is not None]
    if len(pesos) >= 2 and all(p <= 1 for p in pesos) and abs(sum(pesos) - 1) > 0.02:
        descartes.append(
            f"criterios: pesos em fração somam {sum(pesos):.2f} (esperado 1); pesos removidos"
        )
        for c in criterios:
            c["peso"] = None

    obs = (r.observacoes or "").strip()[:300] or None
    campos_db = {
        "criterios": criterios, "teto_orcamento": teto, "teto_por_proponente": teto_prop,
        "percentuais_max": pmax, "percentuais_min": pmin, "rubricas_vedadas": rubricas,
        "limites_caracteres": limites, "campos_exigidos": campos, "contrapartida_exigida": contra,
        "prazo_execucao_meses": prazo, "exige_acessibilidade": acess, "observacoes": obs, "evidencias": evidencias,
    }  # fmt: skip
    return ResultadoExtracao(campos=campos_db, descartes=descartes)


async def extrair(
    doc: Documento, provedor: Provedor, titulo: str | None = None
) -> ResultadoExtracao:
    partes = dividir(doc)
    truncado = len(partes) > MAX_PARTES
    partes = partes[:MAX_PARTES]
    if not partes:
        raise ErroExtracao("documento sem texto")
    respostas: list[RespostaLLM] = []
    for parte in partes:
        # HTML não tem páginas: o modelo não deve citar página
        paginas = parte if doc.tipo == "pdf" else [(1, t) for _, t in parte]
        bruto = await provedor.gerar(montar_prompt(paginas, titulo), SCHEMA_GEMINI)
        try:
            respostas.append(RespostaLLM.model_validate(bruto))
        except ValidationError as exc:
            raise ErroExtracao(
                f"resposta do modelo fora do esquema: {str(exc.errors()[0]['loc'])}"
            ) from exc
    resultado = validar_e_verificar(fundir(respostas), doc)
    resultado.partes = len(partes)
    resultado.truncado = truncado
    for d in resultado.descartes:
        log.info("descartado: %s", d)
    if truncado:
        log.warning("documento muito grande: lidas só as primeiras %d partes", MAX_PARTES)
    return resultado
