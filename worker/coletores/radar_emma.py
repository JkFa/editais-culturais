"""Radar de Editais (Clube Emma): fonte `descoberta`. Lemos SÓ os links oficiais.

Termos e robots.txt conferidos em 07/11/2026: o site declara que as informações do radar são
públicas e o robots.txt libera a home (bloqueia só `/fila-revisao-*` e `/candidatos-*`, que não
tocamos). Descartamos título, prazo, região e classificação deles: cada link novo vira um candidato
na fila, com os dados lidos da página oficial.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..descoberta import candidato_de_link
from ..fila import LIMITE_CANDIDATOS_POR_EXECUCAO, marcador, normalizar_url
from ..web import ClienteWeb
from .base import ResultadoFonte

ORIGEM = "descoberto via Radar de Editais (Clube Emma)"
_RE_BLOCO = re.compile(r"const EDITAIS\s*=\s*\[(.*?)\n\];", re.S)
_RE_ENTRADA = re.compile(r"\{[^{}\n]*?\blink:\s*'([^']+)'[^{}\n]*\}")
_RE_TIPO = re.compile(r"\btipo:\s*'(\w+)'")
# Páginas que não são o edital em si.
_IGNORAR_HOSTS = re.compile(
    r"(instagram|facebook|wa\.me|whatsapp|youtube|linkedin|twitter|x\.com)", re.I
)


def links_oficiais(html: str) -> list[str]:
    """Links dos editais com prazo ou a confirmar. Ignora o bloco de patrocínio contínuo (sem edital)."""
    bloco = _RE_BLOCO.search(html)
    corpo = bloco.group(1) if bloco else html
    vistos: dict[str, None] = {}
    for linha in corpo.split("\n"):
        m = _RE_ENTRADA.search(linha)
        if not m:
            continue
        tipo = _RE_TIPO.search(linha)
        if tipo and tipo.group(1) == "continuo":
            continue
        link = m.group(1).strip()
        if link.startswith("http") and not _IGNORAR_HOSTS.search(link):
            vistos.setdefault(link, None)
    return list(vistos)


async def coletar(
    cliente: ClienteWeb,
    url: str,
    intervalo_s: float,
    urls_conhecidas: set[str],
    marcadores_existentes: set[str],
    limite: int = LIMITE_CANDIDATOS_POR_EXECUCAO,
    ignorar_hosts: tuple[str, ...] = (),
) -> ResultadoFonte:
    res = ResultadoFonte(fonte="radar-clube-emma")
    pagina = await cliente.obter(url, intervalo_s=intervalo_s)
    links = links_oficiais(pagina.texto)
    if not links:
        res.status = "sem_dados"
        res.detalhe = "nenhum link encontrado: a estrutura do radar pode ter mudado"
        return res
    res.estado["total_links"] = len(links)
    for link in links:
        host = urlparse(link).netloc.lower().removeprefix("www.")
        # Prosas: os termos proíbem extração de dados (ver DECISIONS). CultBR/CultEditais: já coletados
        # pela API própria ou atrás de desafio anti-bot, que não contornamos.
        if any(host == h or host.endswith("." + h) for h in ignorar_hosts):
            res.descartes["host_ignorado"] += 1
            continue
        chave_url = normalizar_url(link)
        if chave_url in urls_conhecidas:
            res.descartes["ja_na_base"] += 1
            continue
        chave = f"radar:{chave_url}"
        if marcador(chave) in marcadores_existentes:
            res.descartes["ja_na_fila"] += 1
            continue
        if len(res.candidatos) >= limite:
            res.descartes["limite_por_execucao"] += 1
            continue
        cand = await candidato_de_link(
            cliente, link, chave=chave, origem=ORIGEM, intervalo_s=intervalo_s
        )
        if cand is None:
            res.descartes["pagina_ilegivel"] += 1
            continue
        res.candidatos.append(cand)
    return res
