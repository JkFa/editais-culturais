"""Cliente web educado para coletores de HTML/PDF de terceiros.

Regras (v4): respeita robots.txt (inclusive Crawl-delay), User-Agent identificável com o link do
repositório, requisições sequenciais por domínio com intervalo mínimo, timeout de 30 s e nenhuma
tentativa de contornar bloqueio (sem proxy, sem trocar IP, sem fingir ser navegador).
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.robotparser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from .http import user_agent

log = logging.getLogger("worker.web")

TIMEOUT_S = 30.0
LIMITE_BYTES = 8_000_000  # páginas e PDFs maiores que isso são descartados
MARCAS_DE_DESAFIO = ("just a moment", "cf-chl", "attention required", "verifique se você é humano")


class ErroWeb(Exception):
    """Falha ao obter uma página."""


class BloqueadoPorRobots(ErroWeb):
    """O robots.txt do site não permite o acesso. Nunca contornamos."""


class BloqueioDeAcesso(ErroWeb):
    """O site recusou o robô (403/429/desafio anti-bot). Não contornamos; vira `bloqueada_em_ci`."""


@dataclass
class Resposta:
    url: str
    status: int
    conteudo: bytes
    tipo: str
    etag: str | None = None
    texto_cache: str | None = field(default=None, repr=False)

    @property
    def texto(self) -> str:
        if self.texto_cache is None:
            self.texto_cache = self.conteudo.decode("utf-8", errors="replace")
        return self.texto_cache


class ClienteWeb:
    def __init__(
        self,
        repo_url: str | None = None,
        transporte: httpx.AsyncBaseTransport | None = None,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ua = user_agent(repo_url)
        self._cliente = httpx.AsyncClient(
            headers={
                "User-Agent": self._ua,
                "Accept": "text/html,application/pdf,application/json;q=0.9,*/*;q=0.5",
            },
            timeout=TIMEOUT_S,
            follow_redirects=True,
            transport=transporte,
        )
        self._dormir = dormir
        self._relogio = relogio
        self._travas: dict[str, asyncio.Lock] = {}
        self._ultima: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    async def __aenter__(self) -> ClienteWeb:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._cliente.aclose()

    @staticmethod
    def _dominio(url: str) -> str:
        return urlparse(url).netloc.lower()

    async def _esperar_vez(self, dominio: str, intervalo_s: float) -> None:
        agora = self._relogio()
        falta = self._ultima.get(dominio, -1e9) + intervalo_s - agora
        if falta > 0:
            await self._dormir(falta)
        self._ultima[dominio] = self._relogio()

    async def _carregar_robots(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base in self._robots:
            return self._robots[base]
        leitor: urllib.robotparser.RobotFileParser | None = None
        try:
            r = await self._cliente.get(f"{base}/robots.txt")
            if r.status_code == 200 and "html" not in r.headers.get("content-type", "").lower():
                leitor = urllib.robotparser.RobotFileParser()
                leitor.parse(r.text.splitlines())
            # 404 ou página HTML no lugar do robots.txt: sem regras, acesso permitido.
        except httpx.HTTPError:
            leitor = None
        self._robots[base] = leitor
        return leitor

    async def permitido(self, url: str) -> tuple[bool, float | None]:
        """(pode acessar, Crawl-delay em segundos se o robots.txt declarar)."""
        leitor = await self._carregar_robots(url)
        if leitor is None:
            return True, None
        atraso = leitor.crawl_delay(self._ua) or leitor.crawl_delay("*")
        return leitor.can_fetch(self._ua, url), float(atraso) if atraso else None

    async def obter(
        self,
        url: str,
        intervalo_s: float = 2.0,
        etag: str | None = None,
        respeitar_robots: bool = True,
    ) -> Resposta:
        dominio = self._dominio(url)
        trava = self._travas.setdefault(dominio, asyncio.Lock())
        async with trava:  # sequencial por domínio
            if respeitar_robots:
                pode, atraso = await self.permitido(url)
                if not pode:
                    raise BloqueadoPorRobots(
                        f"robots.txt de {dominio} não permite {urlparse(url).path}"
                    )
                intervalo_s = max(intervalo_s, atraso or 0.0)
            ultimo: Exception | None = None
            for tentativa in (1, 2):
                await self._esperar_vez(dominio, intervalo_s)
                cabecalhos = {"If-None-Match": etag} if etag else {}
                try:
                    r = await self._cliente.get(url, headers=cabecalhos)
                except httpx.HTTPError as exc:
                    ultimo = ErroWeb(f"{type(exc).__name__} {exc}".strip())
                    if tentativa == 1:
                        await self._dormir(3.0)
                        continue
                    raise ultimo from exc
                if r.status_code == 304:
                    return Resposta(str(r.url), 304, b"", "", etag)
                if r.status_code in (403, 429) or self._eh_desafio(r):
                    raise BloqueioDeAcesso(f"HTTP {r.status_code} em {dominio}")
                if r.status_code >= 500 and tentativa == 1:
                    await self._dormir(3.0)
                    continue
                if r.status_code >= 400:
                    raise ErroWeb(f"HTTP {r.status_code} em {url}")
                if len(r.content) > LIMITE_BYTES:
                    raise ErroWeb(f"conteúdo grande demais ({len(r.content)} bytes) em {url}")
                return Resposta(
                    str(r.url),
                    r.status_code,
                    r.content,
                    r.headers.get("content-type", ""),
                    r.headers.get("etag"),
                )
            raise ultimo or ErroWeb("falha desconhecida")

    @staticmethod
    def _eh_desafio(r: httpx.Response) -> bool:
        if r.status_code != 503 and "html" not in r.headers.get("content-type", ""):
            return False
        cabeca = r.text[:3000].lower()
        return any(m in cabeca for m in MARCAS_DE_DESAFIO) and len(r.content) < 20_000
