"""Vigia de página: avisa quando a página de uma fonte que abre edital raramente muda.

Para empresas e institutos com 1 chamada por ano não vale um coletor: guardamos o hash do texto
principal da página e, quando ele muda de forma relevante, abrimos um candidato na fila com um
resumo do que mudou. A primeira execução só cria a linha de base (nunca abre candidato).
"""

from __future__ import annotations

import difflib
import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from ..extrator import extrair_pagina
from ..fila import Candidato
from ..web import ClienteWeb
from .base import ResultadoFonte

MIN_CARACTERES = 400  # menos que isso = página feita em JavaScript ou bloqueada: vigia inútil
MAX_LINHAS = 150
MAX_COLUNAS = 160
MIN_LINHA_NOVA = 25  # ignora ruído (contadores, datas soltas)
_RUIDO = re.compile(r"^(?:\W|\d)+$|cookie|©|todos os direitos|política de privacidade", re.I)


def linhas_principais(texto: str) -> list[str]:
    linhas = [re.sub(r"\s+", " ", x).strip() for x in texto.split("\n")]
    linhas = [x[:MAX_COLUNAS] for x in linhas if len(x) >= 12 and not _RUIDO.search(x)]
    return linhas[:MAX_LINHAS]


def hash_do_texto(linhas: list[str]) -> str:
    return hashlib.sha256("\n".join(linhas).encode()).hexdigest()


def linhas_novas(antes: list[str], depois: list[str]) -> list[str]:
    """Linhas que passaram a existir, só as relevantes (compridas o bastante)."""
    anteriores = set(antes)
    return [x for x in depois if x not in anteriores and len(x) >= MIN_LINHA_NOVA]


async def vigiar(
    cliente: ClienteWeb,
    id_fonte: str,
    nome: str,
    url: str,
    intervalo_s: float,
    estado_anterior: dict[str, Any] | None,
    agora: datetime | None = None,
) -> ResultadoFonte:
    agora = agora or datetime.now(UTC)
    res = ResultadoFonte(fonte=id_fonte)
    r = await cliente.obter(url, intervalo_s=intervalo_s)
    pagina = extrair_pagina(r.texto, r.url)
    if len(pagina.texto) < MIN_CARACTERES:
        res.status = "conteudo_insuficiente"
        res.detalhe = f"só {len(pagina.texto)} caracteres de texto: a página depende de JavaScript"
        return res
    linhas = linhas_principais(pagina.texto)
    novo_hash = hash_do_texto(linhas)
    res.estado = {"hash": novo_hash, "linhas": linhas, "verificado_em": agora.date().isoformat()}
    if not estado_anterior:
        res.detalhe = "linha de base criada"
        return res
    if estado_anterior.get("hash") == novo_hash:
        res.estado["verificado_em"] = agora.date().isoformat()
        return res
    adicionadas = linhas_novas(estado_anterior.get("linhas", []), linhas)
    removidas = [x for x in estado_anterior.get("linhas", []) if x not in set(linhas)]
    if not adicionadas:
        res.detalhe = "mudou, mas sem linhas novas relevantes"
        return res
    diff = [f"+ {x}" for x in adicionadas[:12]] + [f"- {x}" for x in removidas[:5]]
    res.candidatos.append(
        Candidato(
            chave=f"vigia:{id_fonte}:{novo_hash[:12]}",
            titulo=f"A página de {nome} mudou: possível edital novo",
            link_oficial=r.url,
            origem=f"vigia de página: {nome}",
            motivo="o conteúdo principal da página mudou desde a última conferência; veja o resumo abaixo",
            diff=diff,
            campos={"orgao_fomento": nome},
        )
    )
    return res


def sem_ruido(linhas: list[str]) -> list[str]:
    _ = difflib
    return linhas
