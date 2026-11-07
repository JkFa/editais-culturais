"""Cliente HTTP assíncrono com concorrência limitada, retries e respeito a Retry-After."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

log = logging.getLogger("worker.http")

USER_AGENT_BASE = "editais-culturais-bot/1.0"


def user_agent(repo_url: str | None = None) -> str:
    """User-Agent identificável. O link vem de REPO_URL ou de GITHUB_REPOSITORY (no Actions)."""
    repo_url = repo_url or os.environ.get("REPO_URL")
    if not repo_url and os.environ.get("GITHUB_REPOSITORY"):
        repo_url = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}"
    return f"{USER_AGENT_BASE} (+{repo_url})" if repo_url else USER_AGENT_BASE


class ErroColeta(Exception):
    """Falha definitiva ao consultar uma fonte."""


class ClienteHttp:
    def __init__(
        self,
        repo_url: str | None = None,
        concorrencia: int = 4,
        timeout: float = 60.0,
        tentativas: int = 3,
        transporte: httpx.AsyncBaseTransport | None = None,
        pausa_base: float = 1.5,
    ) -> None:
        self._sem = asyncio.Semaphore(concorrencia)
        self._tentativas = tentativas
        self._pausa_base = pausa_base
        self._cliente = httpx.AsyncClient(
            headers={"User-Agent": user_agent(repo_url), "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
            transport=transporte,
        )

    async def __aenter__(self) -> ClienteHttp:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._cliente.aclose()

    @staticmethod
    def _espera_retry_after(resposta: httpx.Response) -> float | None:
        valor = resposta.headers.get("Retry-After")
        if not valor:
            return None
        if valor.isdigit():
            return min(float(valor), 60.0)
        try:
            alvo = parsedate_to_datetime(valor)
            return max(0.0, min((alvo - datetime.now(UTC)).total_seconds(), 60.0))
        except (TypeError, ValueError):
            return None

    async def json(self, url: str, params: dict[str, str] | None = None) -> Any:
        ultimo: Exception | None = None
        for tentativa in range(1, self._tentativas + 1):
            espera = self._pausa_base * 2 ** (tentativa - 1)
            try:
                async with self._sem:
                    resposta = await self._cliente.get(url, params=params)
                if resposta.status_code in (429, 500, 502, 503, 504):
                    espera = self._espera_retry_after(resposta) or espera
                    ultimo = ErroColeta(f"HTTP {resposta.status_code}")
                elif resposta.status_code >= 400:
                    raise ErroColeta(f"HTTP {resposta.status_code} em {url}")
                else:
                    try:
                        return resposta.json()
                    except ValueError as exc:
                        raise ErroColeta(f"resposta não é JSON em {url}") from exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                # Timeouts do httpx têm mensagem vazia: registra também o tipo.
                ultimo = ErroColeta(f"{type(exc).__name__} {exc}".strip())
            if tentativa < self._tentativas:
                log.warning(
                    "tentativa %d/%d falhou (%s); aguardando %.1fs",
                    tentativa,
                    self._tentativas,
                    ultimo,
                    espera,
                )
                await asyncio.sleep(espera)
        raise ErroColeta(f"falha após {self._tentativas} tentativas em {url}: {ultimo}")
