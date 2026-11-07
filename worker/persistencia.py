"""Persistência: snapshot JSON (sempre) e Supabase via REST (quando há credenciais)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .modelos import Edital, Radar

log = logging.getLogger("worker.persistencia")

LOTE = 100
# Colunas que o banco gera ou controla; nunca vão no payload de escrita.
_FORA_DO_PAYLOAD = {"id", "busca_textual", "chave"}


# ---------------------------------------------------------------- snapshot


def ler_snapshot(caminho: Path) -> dict[str, Any] | None:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def editais_do_snapshot(snapshot: dict[str, Any] | None) -> list[Edital]:
    """Reidrata editais de um snapshot anterior (usado quando uma fonte falha)."""
    saida: list[Edital] = []
    for linha in (snapshot or {}).get("editais", []):
        try:
            saida.append(Edital.model_validate(linha))
        except ValueError:
            log.warning("linha inválida no snapshot anterior ignorada: %s", linha.get("chave"))
    return saida


def escrever_snapshot(
    caminho: Path,
    editais: list[Edital],
    radar: list[Radar],
    fontes_ok: list[str],
    fontes_falharam: list[dict[str, str]],
    agora: datetime,
) -> dict[str, Any]:
    """Grava só editais abertos, ordenados por prazo. Escrita atômica."""
    abertos = sorted(
        (e for e in editais if e.inscricao_fim >= agora), key=lambda e: e.inscricao_fim
    )
    corpo = {
        "gerado_em": agora.astimezone(UTC).isoformat(),
        "fontes_ok": sorted(fontes_ok),
        "fontes_falharam": fontes_falharam,
        "editais": [{**e.para_linha(), "chave": e.chave} for e in abertos],
        "radar": [r.model_dump(mode="json") for r in radar],
    }
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(json.dumps(corpo, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    temporario.replace(caminho)
    return corpo


def preservar_carimbos(
    editais: list[Edital], anteriores: dict[tuple[str, str], dict[str, Any]]
) -> list[Edital]:
    """Edital com hash igual ao anterior mantém `coletado_em`/`atualizado_em` antigos.

    Assim "coletado em" significa "última vez que o conteúdo mudou" e é igual no
    Supabase (que não recebe update de linhas idênticas) e no snapshot.
    """
    saida = []
    for e in editais:
        antigo = anteriores.get((e.fonte, e.id_externo))
        if antigo and antigo.get("hash_conteudo") == e.hash_conteudo:
            e = e.model_copy(
                update={
                    "coletado_em": _dt(antigo.get("coletado_em")) or e.coletado_em,
                    "atualizado_em": _dt(antigo.get("atualizado_em")) or e.atualizado_em,
                }
            )
        saida.append(e)
    return saida


def _dt(valor: Any) -> datetime | None:
    if not valor:
        return None
    return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))


# ---------------------------------------------------------------- Supabase


@dataclass
class ConfigSupabase:
    url: str
    chave_servico: str

    @classmethod
    def do_ambiente(cls) -> ConfigSupabase | None:
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        chave = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        return cls(url, chave) if url and chave else None


class Supabase:
    def __init__(
        self, cfg: ConfigSupabase, transporte: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._base = f"{cfg.url}/rest/v1"
        cabecalhos = {"apikey": cfg.chave_servico, "Content-Type": "application/json"}
        # Chaves novas (`sb_secret_...`) não são JWT: vão só em `apikey`. A service_role
        # legada (JWT) também vai em Authorization.
        if not cfg.chave_servico.startswith("sb_"):
            cabecalhos["Authorization"] = f"Bearer {cfg.chave_servico}"
        self._cliente = httpx.AsyncClient(
            headers=cabecalhos,
            timeout=60,
            transport=transporte,
        )

    async def fechar(self) -> None:
        await self._cliente.aclose()

    async def _req(self, metodo: str, caminho: str, **kw: Any) -> httpx.Response:
        ultimo: Exception | None = None
        for tentativa in range(1, 4):
            try:
                r = await self._cliente.request(metodo, f"{self._base}{caminho}", **kw)
                if r.status_code < 500 and r.status_code != 429:
                    if r.status_code >= 400:
                        # O corpo do erro do PostgREST não contém segredos.
                        raise RuntimeError(
                            f"Supabase {metodo} {caminho}: {r.status_code} {r.text[:300]}"
                        )
                    return r
                ultimo = RuntimeError(f"HTTP {r.status_code}")
            except httpx.TransportError as exc:
                ultimo = exc
            await asyncio.sleep(2**tentativa)
        raise RuntimeError(f"Supabase indisponível após 3 tentativas: {ultimo}")

    async def existentes(self) -> dict[tuple[str, str], dict[str, Any]]:
        saida: dict[tuple[str, str], dict[str, Any]] = {}
        deslocamento = 0
        while True:
            r = await self._req(
                "GET",
                "/editais",
                params={
                    "select": "fonte,id_externo,hash_conteudo,coletado_em,atualizado_em",
                    "order": "id.asc",
                    "limit": "1000",
                    "offset": str(deslocamento),
                },
            )
            linhas = r.json()
            for linha in linhas:
                saida[(linha["fonte"], linha["id_externo"])] = linha
            if len(linhas) < 1000:
                return saida
            deslocamento += 1000

    async def upsert_editais(self, editais: list[Edital]) -> int:
        linhas = [
            {k: v for k, v in e.para_linha().items() if k not in _FORA_DO_PAYLOAD} for e in editais
        ]
        for i in range(0, len(linhas), LOTE):
            await self._req(
                "POST",
                "/editais",
                params={"on_conflict": "fonte,id_externo"},
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                content=json.dumps(linhas[i : i + LOTE]),
            )
        return len(linhas)

    async def remover_sumidos(self, fonte: str, ids_vigentes: set[str], agora: datetime) -> int:
        """Remove editais ainda abertos no banco que a fonte não devolve mais.

        Só roda para fontes coletadas com sucesso. Editais já encerrados ficam (a
        função `limpar_editais_antigos` os apaga depois de 180 dias).
        """
        r = await self._req(
            "GET",
            "/editais",
            params={
                "select": "id_externo",
                "fonte": f"eq.{fonte}",
                "inscricao_fim": f"gte.{agora.isoformat()}",
                "limit": "5000",
            },
        )
        sumidos = [x["id_externo"] for x in r.json() if x["id_externo"] not in ids_vigentes]
        for i in range(0, len(sumidos), LOTE):
            lista = ",".join('"' + s.replace('"', '\\"') + '"' for s in sumidos[i : i + LOTE])
            await self._req(
                "DELETE", "/editais", params={"fonte": f"eq.{fonte}", "id_externo": f"in.({lista})"}
            )
        return len(sumidos)

    async def sincronizar_radar(self, radar: list[Radar], agora: datetime) -> None:
        linhas = [{**r.model_dump(mode="json"), "atualizado_em": agora.isoformat()} for r in radar]
        if linhas:
            await self._req(
                "POST",
                "/radar_editais",
                params={"on_conflict": "id_externo"},
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                content=json.dumps(linhas),
            )
        atuais = (await self._req("GET", "/radar_editais", params={"select": "id_externo"})).json()
        vigentes = {r.id_externo for r in radar}
        for x in atuais:
            if x["id_externo"] not in vigentes:
                await self._req(
                    "DELETE", "/radar_editais", params={"id_externo": f"eq.{x['id_externo']}"}
                )

    async def heartbeat(self, agora: datetime) -> None:
        await self._req(
            "POST",
            "/heartbeat",
            params={"on_conflict": "id"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            content=json.dumps([{"id": 1, "ultimo_ping": agora.isoformat()}]),
        )

    async def limpar_antigos(self) -> None:
        await self._req("POST", "/rpc/limpar_editais_antigos", content="{}")
