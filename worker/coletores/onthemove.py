"""On the Move (on-the-move.org): chamadas abertas de mobilidade, residência e financiamento.

Verificado em 07/11/2026. O site oferece feeds RSS por busca ("Subscribe to an RSS feed for this
search"): `news/<tipo>/<país>/<online>/<perfil>/<disciplina>/<tema>/feed`, onde cada faceta é um id
numérico ou `all`. O feed de todas as chamadas traz título, texto, prazo (`<time datetime>`) e o link
para a chamada original, mas não traz tipo nem disciplina. Para isso lemos um feed por tipo e um por
disciplina (cada um filtra o mesmo conjunto) e cruzamos pelo link. O `robots.txt` só bloqueia
`?f[...]`; os caminhos de feed são permitidos.

Só guardamos título, um trecho curto, prazo e links para o original; nunca o texto integral.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from html import unescape
from urllib.parse import urlparse

from ..elegibilidade import analisar_elegibilidade, custos_cobertos
from ..modelos import LINGUAGENS, EditalBruto, Proveniencia
from ..padronizacao import FUSO_PADRAO, limpar_texto, truncar_palavra
from ..paises import codigo_do_pais
from ..web import BloqueadoPorRobots, BloqueioDeAcesso, ClienteWeb, ErroWeb
from .base import ResultadoFonte

log = logging.getLogger("worker.onthemove")

FONTE = "onthemove"
ORGAO_PADRAO = "On the Move (ver chamada original)"
LIMITE_TRECHO = 320

# Ids das facetas do site (conferidos em on-the-move.org/news em 07/11/2026), em ordem de prioridade
# quando uma chamada aparece em mais de um tipo.
TIPOS = {
    7: ("Residencies", "residencia"),
    3: ("Project Funding", "projeto"),
    2: ("Competitions & Awards", "premio"),
    4: ("Presenting Work", "apresentacao"),
    81: ("Fellowships", "formacao"),
    8: ("Training", "formacao"),
    6: ("Meetings & Collaboration", "outro"),
}
DISCIPLINAS = {
    74: ("Architecture", "Multiáreas"),
    30: ("Cross-disciplinary", "Multiáreas"),
    124: ("Cultural Heritage", "Patrimônio"),
    33: ("Digital / New Media", "Multiáreas"),
    35: ("Film", "Cinema & Audiovisual"),
    36: ("Literature", "Literatura"),
    37: ("Music & Sound", "Música"),
    38: ("Performing Arts", "Teatro & Dança"),
    32: ("Visual Arts & Design", "Artes Visuais"),
}
ONLINE = 49  # "Online or Remote"

_RE_PRAZO = re.compile(r'Deadline:\s*<time[^>]*datetime="([^"]+)"', re.I)
_RE_APLICAR = re.compile(r"More info and apply:</b>\s*<a href=\"([^\"]+)\"", re.I)
_RE_PAIS_NO_TITULO = re.compile(r"\(([^()]+)\)\s*$")


def url_do_feed(base: str, tipo: int | None = None, disciplina: int | None = None,
                online: int | None = None) -> str:  # fmt: skip
    partes = [tipo or "all", "all", online or "all", "all", disciplina or "all", "all"]
    return f"{base.rstrip('/')}/news/{'/'.join(str(p) for p in partes)}/feed"


def _slug(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def paises_do_titulo(titulo: str) -> str | None:
    """Códigos ISO do país entre parênteses no fim do título ("(Belgium, Croatia)" -> "BE,HR")."""
    m = _RE_PAIS_NO_TITULO.search(titulo)
    if not m:
        return None
    nomes = [n for n in re.split(r",|\band\b|&", m.group(1)) if n.strip()]
    codigos = [codigo_do_pais(n) for n in nomes]
    if not codigos or any(c is None for c in codigos):
        return None  # "(3rd call)" e afins não são país
    return ",".join(dict.fromkeys(c for c in codigos if c))


def prazo_do_html(html: str) -> datetime | None:
    m = _RE_PRAZO.search(html)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _fim_do_dia(prazo: datetime) -> datetime:
    """Prazo só com data: vale até 23:59:59 do dia, no fuso de Brasília (padrão do portal)."""
    dia = prazo.date()
    return datetime(dia.year, dia.month, dia.day, 23, 59, 59, tzinfo=FUSO_PADRAO).astimezone(UTC)


def ler_feed(xml: str) -> list[dict[str, str]]:
    """Itens do feed: título, link, html da descrição (ainda com tags) e data de publicação."""
    raiz = ET.fromstring(xml)
    itens = []
    for it in raiz.iter("item"):
        titulo_bruto = (
            ET.tostring(it.find("title"), encoding="unicode", method="text")
            if it.find("title") is not None
            else ""
        )
        titulo = re.sub(r"^\s*\[[^\]]+\]\s*", "", limpar_texto(unescape(titulo_bruto)))
        link = (it.findtext("link") or "").split("?")[0].strip()
        if not titulo or not link:
            continue
        itens.append(
            {
                "titulo": titulo,
                "link": link,
                "html": unescape(it.findtext("description") or ""),
                "publicado": it.findtext("pubDate") or "",
            }
        )
    return itens


def _texto_em_blocos(html: str) -> str:
    """Texto do HTML da descrição, com uma linha por parágrafo/item (o classificador lê frase a frase)."""
    html = re.sub(r"</(p|li|div|h\d|ul|ol)>|<br\s*/?>", "\n", html, flags=re.I)
    linhas = (limpar_texto(unescape(re.sub(r"<[^>]+>", " ", x))) for x in html.split("\n"))
    return "\n".join(x for x in linhas if x)


def _organizacao(titulo: str) -> str:
    """Muitos títulos seguem "Organização: Nome da chamada". Senão, não inventamos."""
    antes, sep, _ = titulo.partition(": ")
    return antes if sep and 3 <= len(antes) <= 70 else ORGAO_PADRAO


def converter_item(
    item: dict[str, str],
    agora: datetime,
    tipo: str | None = None,
    linguagens: list[str] | None = None,
    online: bool = False,
    prazo_pagina: datetime | None = None,
    fonte: str = FONTE,
) -> tuple[EditalBruto | None, str]:
    """(edital, motivo de descarte). Sem prazo comprovável ou já vencido não publicamos."""
    html = item["html"]
    prazo = prazo_do_html(html) or prazo_pagina
    if prazo is None:
        return None, "sem_prazo"
    fim = _fim_do_dia(prazo)
    if fim <= agora:
        return None, "encerrado"
    texto = limpar_texto(html)
    texto_chamada = _texto_em_blocos(html).split("More info and apply:")[0]
    elegibilidade, frase = analisar_elegibilidade(texto_chamada)
    custos = custos_cobertos(texto_chamada)
    primeiro = limpar_texto(re.split(r"</p>", html, maxsplit=1)[0])
    titulo = item["titulo"]
    pais = paises_do_titulo(titulo) or ("Online" if online else None)
    prov = {
        "inscricao_fim": Proveniencia(via="api", trecho=_trecho_prazo(texto)),
    }
    if frase:
        prov["elegibilidade_br"] = Proveniencia(via="regex", trecho=frase)
    aplicar = _RE_APLICAR.search(html)
    return (
        EditalBruto(
            fonte=fonte,
            id_externo=_slug(item["link"]),
            url_origem=item["link"],
            url_inscricao=aplicar.group(1) if aplicar else None,
            titulo=titulo,
            orgao_fomento=_organizacao(titulo),
            resumo_original=truncar_palavra(primeiro, LIMITE_TRECHO) or None,
            areas=[],
            inscricao_fim=fim,
            fim_tem_horario=False,
            abrangencia=None,
            via="On the Move",
            linguagens=linguagens or ["Multiáreas"],
            ambito="internacional",
            pais_destino=pais,
            elegibilidade_br=elegibilidade,  # type: ignore[arg-type]
            tipo_oportunidade=tipo,  # type: ignore[arg-type]
            custos_cobertos=custos,
            idioma_original="en",
            prazo_original=prazo,
            titulo_original=titulo,
            proveniencia=prov,
        ),
        "",
    )


def _trecho_prazo(texto: str) -> str:
    m = re.search(r"Deadline:\s*([^|]{4,40}?\d{4})", texto)
    return (f"Deadline: {m.group(1).strip()}" if m else "Deadline (campo datetime do feed)")[:300]


async def _feed(cliente: ClienteWeb, url: str, intervalo_s: float) -> list[dict[str, str]]:
    r = await cliente.obter(url, intervalo_s=intervalo_s)
    return ler_feed(r.texto)


async def coletar(
    cliente: ClienteWeb,
    url_base: str,
    intervalo_s: float,
    agora: datetime | None = None,
    fonte: str = FONTE,
) -> ResultadoFonte:
    agora = agora or datetime.now(UTC)
    res = ResultadoFonte(fonte=fonte)
    itens = await _feed(cliente, url_do_feed(url_base), intervalo_s)
    if not itens:
        res.status = "sem_dados"
        res.detalhe = "o feed de chamadas abertas veio vazio"
        return res
    por_link = {i["link"]: i for i in itens}  # um por URL canônica
    falhas_facetas: list[str] = []

    async def membros(**faceta: int) -> set[str]:
        try:
            return {
                i["link"]
                for i in await _feed(cliente, url_do_feed(url_base, **faceta), intervalo_s)
            }
        except (BloqueadoPorRobots, BloqueioDeAcesso):
            raise
        except (ErroWeb, ET.ParseError) as exc:
            falhas_facetas.append(f"{faceta}: {type(exc).__name__}")
            return set()

    tipo_de: dict[str, str] = {}
    for id_, (_, valor) in TIPOS.items():  # ordem de prioridade
        for link in await membros(tipo=id_):
            tipo_de.setdefault(link, valor)
    ling_de: dict[str, list[str]] = {}
    for id_, (_, valor) in DISCIPLINAS.items():
        for link in await membros(disciplina=id_):
            if valor not in ling_de.setdefault(link, []):
                ling_de[link].append(valor)
    online_de = await membros(online=ONLINE)
    for lista in ling_de.values():  # "Multiáreas" só fica sozinha; ordem da taxonomia
        if len(lista) > 1:
            lista[:] = sorted((x for x in lista if x != "Multiáreas"), key=LINGUAGENS.index)

    for link, item in por_link.items():
        prazo_pagina = None
        if prazo_do_html(item["html"]) is None:  # única tentativa na página da chamada
            try:
                prazo_pagina = prazo_do_html(
                    (await cliente.obter(link, intervalo_s=intervalo_s)).texto
                )
            except (ErroWeb, BloqueioDeAcesso):
                prazo_pagina = None
        bruto, motivo = converter_item(
            item,
            agora,
            tipo_de.get(link),
            ling_de.get(link),
            link in online_de,
            prazo_pagina,
            fonte,
        )
        if bruto:
            res.brutos.append(bruto)
            res.textos[bruto.id_externo] = f"{bruto.titulo} {bruto.resumo_original or ''}"
        else:
            res.descartes[motivo] += 1
    if falhas_facetas:
        res.detalhe = "feeds de tipo/disciplina com falha: " + "; ".join(falhas_facetas)[:250]
    return res
