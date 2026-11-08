"""Executa as fontes novas (v4): oficiais, plataformas, descoberta e vigias de página.

Cada fonte roda isolada: uma falha vira `fontes_status`, nunca derruba as demais. Domínios
diferentes rodam em paralelo; dentro de um domínio o `ClienteWeb` serializa e respeita o
intervalo e o Crawl-delay.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .catalogo import FonteConfig, StatusFonte, classificar_falha, status_de, status_desativada
from .coletores import cultbr, govbr, minc, onthemove, radar_emma, vigia
from .coletores.base import ResultadoFonte
from .fila import Candidato
from .web import BloqueadoPorRobots, BloqueioDeAcesso, ClienteWeb

log = logging.getLogger("worker.fontes_v4")


@dataclass
class ResultadoV4:
    resultados: dict[str, ResultadoFonte] = field(default_factory=dict)
    status: list[StatusFonte] = field(default_factory=list)
    estado_vigias: dict[str, Any] = field(default_factory=dict)
    falhas: list[dict[str, str]] = field(default_factory=list)

    @property
    def candidatos(self) -> list[tuple[str, Candidato]]:
        return [(fid, c) for fid, r in self.resultados.items() for c in r.candidatos]


async def _rodar_uma(
    cliente: ClienteWeb,
    f: FonteConfig,
    agora: datetime,
    urls_conhecidas: set[str],
    marcadores: set[str],
    estado_vigias: dict[str, Any],
) -> ResultadoFonte:
    intervalo = f.intervalo_min_entre_requisicoes_s
    if f.metodo == "vigia":
        return await vigia.vigiar(
            cliente, f.id, f.nome, f.url, intervalo, estado_vigias.get(f.id), agora
        )
    if f.coletor == "minc":
        return await minc.coletar(cliente, f.url, intervalo, agora, f.id)
    if f.coletor == "govbr":
        return await govbr.coletar(cliente, f, intervalo, agora)
    if f.coletor == "onthemove":
        return await onthemove.coletar(cliente, f.url, intervalo, agora, f.id)
    if f.coletor == "cultbr":
        return await cultbr.coletar(
            cliente, f.url, intervalo, urls_conhecidas, marcadores, agora, f.id
        )
    if f.coletor == "radar_emma":
        return await radar_emma.coletar(
            cliente,
            f.url,
            intervalo,
            urls_conhecidas,
            marcadores,
            ignorar_hosts=tuple(f.ignorar_hosts),
        )
    raise ValueError(f"fonte {f.id}: coletor desconhecido ({f.coletor!r}, método {f.metodo})")


async def rodar_fontes_v4(
    fontes: list[FonteConfig],
    agora: datetime,
    urls_conhecidas: set[str],
    marcadores: set[str],
    estado_vigias: dict[str, Any],
    status_anterior: dict[str, dict[str, Any]],
    apenas: str | None = None,
    cliente: ClienteWeb | None = None,
) -> ResultadoV4:
    out = ResultadoV4()
    escolhidas = [f for f in fontes if apenas is None or f.id == apenas]
    ativas = [f for f in escolhidas if f.ativa]
    out.status += [status_desativada(f) for f in escolhidas if not f.ativa]
    proprio = cliente is None
    cliente = cliente or ClienteWeb()

    def duracao(inicio: float) -> int:
        return int((time.monotonic() - inicio) * 1000)

    async def uma(f: FonteConfig) -> None:
        inicio = time.monotonic()
        anterior = status_anterior.get(f.id)
        try:
            res = await _rodar_uma(cliente, f, agora, urls_conhecidas, marcadores, estado_vigias)
            out.resultados[f.id] = res
            if res.estado:
                out.estado_vigias[f.id] = res.estado
            total = len(res.brutos) or int(res.estado.get("total_links", 0) or 0)
            erro = res.detalhe if res.status != "ok" else None
            out.status.append(
                status_de(
                    f,
                    res.status,  # type: ignore[arg-type]
                    anterior,
                    erro=erro,
                    total=total,
                    candidatos=len(res.candidatos),
                    duracao_ms=duracao(inicio),
                    agora=agora,
                )
            )
        except BloqueioDeAcesso as exc:
            out.falhas.append({"fonte": f.id, "erro": str(exc)})
            out.status.append(
                status_de(f, "bloqueada_em_ci", anterior, erro=str(exc), agora=agora,
                          duracao_ms=duracao(inicio))
            )  # fmt: skip
        except BloqueadoPorRobots as exc:
            out.falhas.append({"fonte": f.id, "erro": str(exc)})
            out.status.append(
                status_de(f, "bloqueada_por_robots", anterior, erro=str(exc), agora=agora)
            )
        except Exception as exc:  # noqa: BLE001 - isolamento por fonte
            texto = f"{type(exc).__name__}: {exc}"[:300]
            log.error("fonte %s falhou: %s", f.id, texto)
            out.falhas.append({"fonte": f.id, "erro": texto})
            estado, texto = classificar_falha(texto, anterior, agora)
            out.status.append(
                status_de(f, estado, anterior, erro=texto, agora=agora, duracao_ms=duracao(inicio))
            )

    try:
        await asyncio.gather(*(uma(f) for f in ativas))
    finally:
        if proprio:
            await cliente.__aexit__()
    return out
