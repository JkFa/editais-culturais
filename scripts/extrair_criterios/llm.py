"""Cliente do Gemini (camada gratuita) para a extração. Segredo só por variável de ambiente."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Protocol

import httpx

log = logging.getLogger("extrair_criterios.llm")

URL_API = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
MODELO_PADRAO = "gemini-3.6-flash"  # o mesmo Flash estável do assistente (ver DECISIONS.md)


class ErroLlm(Exception):
    def __init__(self, tipo: str, mensagem: str) -> None:
        super().__init__(mensagem)
        self.tipo = tipo  # config | cota | indisponivel | resposta


class Provedor(Protocol):
    modelo: str

    async def gerar(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


class ProvedorGemini:
    def __init__(
        self,
        chave: str,
        modelo: str = MODELO_PADRAO,
        intervalo_s: float = 4.5,
        transporte: httpx.AsyncBaseTransport | None = None,
        dormir=asyncio.sleep,
    ) -> None:
        self.modelo = modelo
        self._intervalo = intervalo_s
        self._dormir = dormir
        self._cliente = httpx.AsyncClient(
            headers={"x-goog-api-key": chave}, timeout=120, transport=transporte
        )
        self._ultima = 0.0

    @classmethod
    def do_ambiente(cls, **kw: Any) -> ProvedorGemini | None:
        chave = os.environ.get("GEMINI_API_KEY", "").strip()
        modelo = os.environ.get("GEMINI_MODEL", "").strip() or MODELO_PADRAO
        return cls(chave, modelo, **kw) if chave else None

    async def fechar(self) -> None:
        await self._cliente.aclose()

    async def gerar(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        espera = self._intervalo - (
            loop.time() - self._ultima
        )  # mantém o ritmo dentro do limite gratuito
        if espera > 0:
            await self._dormir(espera)
        self._ultima = loop.time()
        corpo = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "temperature": 0,
            },
        }
        ultimo = ""
        for tentativa in range(1, 4):
            try:
                r = await self._cliente.post(URL_API.format(modelo=self.modelo), json=corpo)
            except httpx.HTTPError as exc:
                ultimo = type(exc).__name__
                await self._dormir(5 * tentativa)
                continue
            if r.status_code in (401, 403):
                raise ErroLlm("config", "chave do Gemini recusada")
            if r.status_code == 429:
                if tentativa == 3:
                    raise ErroLlm("cota", "limite da camada gratuita do Gemini atingido")
                await self._dormir(min(float(r.headers.get("Retry-After", 20)), 60))
                continue
            if r.status_code >= 500:
                ultimo = f"HTTP {r.status_code}"
                await self._dormir(5 * tentativa)
                continue
            if r.status_code >= 400:
                raise ErroLlm("resposta", f"HTTP {r.status_code}")
            try:
                texto = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                dados = json.loads(texto)
            except (KeyError, IndexError, ValueError) as exc:
                raise ErroLlm("resposta", "resposta do modelo sem JSON válido") from exc
            if not isinstance(dados, dict):
                raise ErroLlm("resposta", "a resposta do modelo não é um objeto")
            return dados
        raise ErroLlm("indisponivel", f"Gemini indisponível ({ultimo})")
