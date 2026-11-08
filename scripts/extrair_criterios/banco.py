"""Acesso ao Supabase (PostgREST, chave de serviço) para a fila e os critérios."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from worker.persistencia import Supabase


def _dt(valor: Any) -> datetime | None:
    if not valor:
        return None
    return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))


class Banco(Supabase):
    async def _todas(self, caminho: str, params: dict[str, str]) -> list[dict[str, Any]]:
        saida: list[dict[str, Any]] = []
        deslocamento = 0
        while True:
            r = await self._req(
                "GET", caminho, params={**params, "limit": "1000", "offset": str(deslocamento)}
            )
            lote = r.json()
            saida += lote
            if len(lote) < 1000:
                return saida
            deslocamento += 1000

    async def editais_abertos(self) -> list[dict[str, Any]]:
        return await self._todas(
            "/editais_abertos",
            {
                "select": "id,titulo,url_origem,url_edital_pdf,inscricao_fim,ambito",
                "order": "id.asc",
            },
        )

    async def fila(self) -> dict[int, dict[str, Any]]:
        linhas = await self._todas(
            "/extracao_fila",
            {
                "select": "edital_id,status,tentativas,ultimo_erro,atualizado_em",
                "order": "edital_id.asc",
            },
        )
        return {x["edital_id"]: {**x, "atualizado_em": _dt(x["atualizado_em"])} for x in linhas}

    async def criterios_existentes(self) -> dict[int, dict[str, Any]]:
        linhas = await self._todas(
            "/edital_criterios",
            {"select": "edital_id,fonte_url,fonte_hash,revisado", "order": "edital_id.asc"},
        )
        return {x["edital_id"]: x for x in linhas}

    async def projetos_por_edital(self) -> Counter[int]:
        linhas = await self._todas(
            "/projetos", {"select": "edital_id", "edital_id": "not.is.null", "order": "id.asc"}
        )
        return Counter(x["edital_id"] for x in linhas)

    async def enfileirar(self, ids: list[int]) -> None:
        if ids:
            await self._req("POST", "/extracao_fila", params={"on_conflict": "edital_id"},
                            json=[{"edital_id": i, "status": "pendente"} for i in ids],
                            headers={"Prefer": "resolution=ignore-duplicates,return=minimal"})  # fmt: skip

    async def marcar(
        self, edital_id: int, status: str, erro: str | None = None, tentativas: int | None = None
    ) -> None:
        corpo: dict[str, Any] = {
            "status": status,
            "ultimo_erro": (erro or None) and erro[:400],
            "atualizado_em": datetime.now(UTC).isoformat(),
        }
        if tentativas is not None:
            corpo["tentativas"] = tentativas
        await self._req(
            "PATCH",
            "/extracao_fila",
            params={"edital_id": f"eq.{edital_id}"},
            json=corpo,
            headers={"Prefer": "return=minimal"},
        )

    async def salvar_criterios(self, linha: dict[str, Any]) -> None:
        await self._req("POST", "/edital_criterios", params={"on_conflict": "edital_id"}, json=linha,
                        headers={"Prefer": "resolution=merge-duplicates,return=minimal"})  # fmt: skip
