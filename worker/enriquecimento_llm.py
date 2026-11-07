"""Enriquecimento opcional por LLM (Gemini via REST). Só roda com GEMINI_API_KEY.

Regra de integridade (seção 1 do projeto): nada vindo do modelo entra sem evidência.
O modelo devolve, para cada campo, o trecho que o comprova. O worker só aceita o
campo se (1) o trecho existe literalmente no texto-fonte e (2) no caso de valores em
R$, o trecho contém o número. O que o regex já preencheu nunca é sobrescrito.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from .modelos import LIMITE_RESUMO_SIMPLES, PROPONENTES, Edital, Proveniencia
from .padronizacao import trecho_comprova_valor, truncar_palavra

log = logging.getLogger("worker.llm")

PROMPT_VERSAO = "v1"
MODELO_PADRAO = "gemini-2.5-flash"
URL_API = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"

INSTRUCAO = """Você extrai dados de editais culturais brasileiros. Responda SOMENTE com JSON.
Regras: use apenas o texto fornecido; se não houver informação, use null. Nunca invente.
Para cada campo marcado com "trecho", copie LITERALMENTE do texto o trecho que o comprova.
Formato:
{
 "resumo_simples": "frase direta (até 250 caracteres) sobre o que o edital financia" | null,
 "valor_total": {"valor": número em reais, "trecho": "..."} | null,
 "teto_por_projeto": {"valor": número em reais, "trecho": "..."} | null,
 "proponentes": [{"codigo": "PF|MEI|PJ_COM_FINS|PJ_SEM_FINS|COLETIVO", "trecho": "..."}],
 "exige_contrapartida": {"valor": true|false, "trecho": "..."} | null
}
valor_total é o montante global do edital; teto_por_projeto é o máximo por projeto/contemplado."""


class _ValorComTrecho(BaseModel):
    valor: float
    trecho: str


class _ProponenteLLM(BaseModel):
    codigo: str
    trecho: str


class _ContrapartidaLLM(BaseModel):
    valor: bool
    trecho: str


class RespostaLLM(BaseModel):
    resumo_simples: str | None = None
    valor_total: _ValorComTrecho | None = None
    teto_por_projeto: _ValorComTrecho | None = None
    proponentes: list[_ProponenteLLM] = Field(default_factory=list)
    exige_contrapartida: _ContrapartidaLLM | None = None


class ResultadoValidado(BaseModel):
    """Só o que passou na validação determinística; é o que vai para o cache."""

    resumo_simples: str | None = None
    valor_total: tuple[Decimal, str] | None = None
    teto_por_projeto: tuple[Decimal, str] | None = None
    proponentes: dict[str, str] = Field(default_factory=dict)
    exige_contrapartida: tuple[bool, str] | None = None


def _norm(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip().lower()


def _trecho_no_texto(trecho: str, texto_fonte: str) -> bool:
    t = _norm(trecho)
    return len(t) >= 5 and t in _norm(texto_fonte)


def validar_resposta(bruta: dict[str, Any], texto_fonte: str) -> ResultadoValidado:
    """Aplica a regra de evidência. Campo reprovado é descartado, não corrigido."""
    try:
        resp = RespostaLLM.model_validate(bruta)
    except ValidationError as exc:
        log.warning("resposta do LLM fora do esquema: %s", exc.errors()[:2])
        return ResultadoValidado()
    saida = ResultadoValidado()
    if resp.resumo_simples:
        resumo = re.sub(r"\s+", " ", resp.resumo_simples).strip()
        saida.resumo_simples = truncar_palavra(resumo, LIMITE_RESUMO_SIMPLES) or None
    for campo in ("valor_total", "teto_por_projeto"):
        item: _ValorComTrecho | None = getattr(resp, campo)
        if item is None:
            continue
        try:
            valor = Decimal(str(item.valor)).quantize(Decimal("0.01"))
        except InvalidOperation:
            continue
        if valor <= 0 or not _trecho_no_texto(item.trecho, texto_fonte):
            continue
        if not trecho_comprova_valor(valor, item.trecho):
            log.info("LLM: %s=%s descartado, trecho não contém o número", campo, valor)
            continue
        setattr(saida, campo, (valor, item.trecho))
    if (
        saida.valor_total
        and saida.teto_por_projeto
        and saida.valor_total[0] < saida.teto_por_projeto[0]
    ):
        saida.valor_total = None
    for p in resp.proponentes:
        if p.codigo in PROPONENTES and _trecho_no_texto(p.trecho, texto_fonte):
            saida.proponentes.setdefault(p.codigo, p.trecho)
    if resp.exige_contrapartida and _trecho_no_texto(resp.exige_contrapartida.trecho, texto_fonte):
        saida.exige_contrapartida = (
            resp.exige_contrapartida.valor,
            resp.exige_contrapartida.trecho,
        )
    return saida


def aplicar(edital: Edital, r: ResultadoValidado) -> Edital:
    """Preenche apenas campos ainda nulos (regex tem prioridade) e registra a proveniência."""
    dados: dict[str, Any] = {}
    prov = dict(edital.proveniencia)
    if r.resumo_simples:
        dados["resumo_simples"] = r.resumo_simples
        prov["resumo_simples"] = Proveniencia(via="llm", trecho=None)
    for campo in ("valor_total", "teto_por_projeto"):
        achado = getattr(r, campo)
        if achado and getattr(edital, campo) is None:
            dados[campo] = achado[0]
            prov[campo] = Proveniencia(via="llm", trecho=achado[1])
    if r.proponentes and not edital.proponentes:
        ordem = [p for p in PROPONENTES if p in r.proponentes]
        dados["proponentes"] = ordem
        prov["proponentes"] = Proveniencia(
            via="llm", trecho=" | ".join(r.proponentes[p] for p in ordem)
        )
    if r.exige_contrapartida and edital.exige_contrapartida is None:
        dados["exige_contrapartida"], trecho = r.exige_contrapartida
        prov["exige_contrapartida"] = Proveniencia(via="llm", trecho=trecho)
    if (
        dados.get("valor_total") is not None
        and (dados.get("teto_por_projeto") or edital.teto_por_projeto) is not None
    ):
        teto = dados.get("teto_por_projeto") or edital.teto_por_projeto
        if dados["valor_total"] < teto:
            dados.pop("valor_total")
            prov.pop("valor_total", None)
    novo = edital.model_copy(update={**dados, "proveniencia": prov})
    return novo.model_copy(update={"hash_conteudo": novo.calcular_hash()})


def chave_cache(texto_fonte: str, modelo: str) -> str:
    return hashlib.sha256(f"{PROMPT_VERSAO}|{modelo}|{texto_fonte}".encode()).hexdigest()


class CacheLLM:
    """Cache em arquivo JSON (commitado junto ao snapshot) indexado pelo hash do texto-fonte."""

    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho
        self._dados: dict[str, Any] = {}
        self._usadas: set[str] = set()
        if caminho.exists():
            try:
                self._dados = json.loads(caminho.read_text(encoding="utf-8"))
            except ValueError:
                log.warning("cache LLM corrompido; ignorando")

    def obter(self, chave: str) -> ResultadoValidado | None:
        self._usadas.add(chave)
        bruto = self._dados.get(chave)
        return ResultadoValidado.model_validate(bruto) if bruto is not None else None

    def guardar(self, chave: str, r: ResultadoValidado) -> None:
        self._usadas.add(chave)
        self._dados[chave] = json.loads(r.model_dump_json())

    def salvar(self) -> None:
        # Descarta entradas de editais que saíram da base, para o arquivo não crescer.
        mantidas = {k: v for k, v in self._dados.items() if k in self._usadas}
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        texto = json.dumps(mantidas, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
        if not self.caminho.exists() or self.caminho.read_text(encoding="utf-8") != texto:
            self.caminho.write_text(texto, encoding="utf-8")


class ClienteGemini:
    def __init__(
        self,
        api_key: str,
        modelo: str = MODELO_PADRAO,
        intervalo_s: float = 4.5,
        transporte: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.modelo = modelo
        self._intervalo = intervalo_s
        self._cliente = httpx.AsyncClient(
            headers={"x-goog-api-key": api_key}, timeout=60, transport=transporte
        )
        self._ultima = 0.0

    async def fechar(self) -> None:
        await self._cliente.aclose()

    async def extrair(self, texto_fonte: str) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()
        espera = self._intervalo - (loop.time() - self._ultima)
        if espera > 0:
            await asyncio.sleep(espera)
        self._ultima = loop.time()
        corpo = {
            "contents": [
                {"parts": [{"text": f"{INSTRUCAO}\n\nTEXTO DO EDITAL:\n{texto_fonte[:12000]}"}]}
            ],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        }
        for tentativa in (1, 2):
            try:
                r = await self._cliente.post(URL_API.format(modelo=self.modelo), json=corpo)
                if r.status_code == 429 and tentativa == 1:
                    await asyncio.sleep(min(float(r.headers.get("Retry-After", 20)), 60))
                    continue
                r.raise_for_status()
                texto = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                dados = json.loads(texto)
                return dados if isinstance(dados, dict) else None
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                log.warning("Gemini falhou (%s); seguindo com regex", type(exc).__name__)
                return None
        return None


async def enriquecer_editais(
    editais: list[Edital],
    textos: dict[str, str],
    cliente: ClienteGemini,
    cache: CacheLLM,
    maximo_chamadas: int,
) -> list[Edital]:
    """Enriquece editais de coleta (não os curados). Erro em um não afeta os demais."""
    saida: list[Edital] = []
    chamadas = 0
    for ed in editais:
        texto = textos.get(f"{ed.fonte}:{ed.id_externo}")
        if not texto:
            saida.append(ed)
            continue
        chave = chave_cache(texto, cliente.modelo)
        resultado = cache.obter(chave)
        if resultado is None and chamadas < maximo_chamadas:
            chamadas += 1
            bruta = await cliente.extrair(texto)
            if bruta is not None:
                resultado = validar_resposta(bruta, texto)
                cache.guardar(chave, resultado)
        saida.append(aplicar(ed, resultado) if resultado else ed)
    log.info("LLM: %d chamadas (limite %d)", chamadas, maximo_chamadas)
    return saida
