"""Órgãos federais em gov.br (Funarte, Biblioteca Nacional, Palmares, IPHAN...): um coletor configurável por fonte.

O gov.br é um Plone, mas cada órgão monta a lista de um jeito. Formatos (campo `formato` em fontes.yaml):
  - `cards`: `a.govbr-card-content`, como o MinC e a Funarte;
  - `links`: links do miolo da página cujo texto parece edital (Palmares);
  - `volto`: páginas em React cujo estado vem embutido no HTML (`window.__data`), como o IPHAN: título, link e data
    de cada item vêm do estado, e o PDF do edital é um item filho do tipo "Arquivo".

Só publica edital com PRAZO FUTURO lido do texto da página ou do PDF, com o trecho que o comprova (como o MinC).
Sem prazo legível vira candidato para conferência humana. O que não é fomento (contratação de consultor, processo seletivo,
mestrado, resultado, errata, programas ibero-americanos) é descartado pelo título.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

from ..extrator import Pagina, extrair_pagina, texto_de_pdf
from ..fila import Candidato
from ..modelos import EditalBruto, Proveniencia
from ..padronizacao import limpar_texto, truncar_palavra
from ..prazos_texto import achar_prazo
from ..taxonomia import sem_acento
from ..web import ClienteWeb, ErroWeb
from .base import ResultadoFonte
from .minc import _descricao, parse_listagem

log = logging.getLogger("worker.govbr")
logging.getLogger("pypdf").setLevel(
    logging.ERROR
)  # avisos de fontes embutidas poluem o log e não afetam o texto

MAX_ITENS_PADRAO = 30
MAX_PDFS_POR_EDITAL = 2

# O que NÃO é fomento para artistas e agentes culturais (aplicado ao título e ao final do endereço).
NAO_E_FOMENTO = re.compile(
    r"\b(consultor\w*|processo seletivo|estagi\w+|mestrado|doutorado|inicia[cç][aã]o cient\w+|pesquisa cient\w+|"
    r"resultado\w*|extrato\w*|retifica[cç]\w+|errata|homologa[cç]\w+|convoca[cç][aã]o|vagas?|perfil|"
    r"parecer\w*|apresenta[cç][aã]o|projeto educativo|cooperacao tecnica|cooperação técnica|inova[cç][aã]o aberta|consulta preliminar|dispensa|justificativa|"
    r"ibermusicas|ibermúsicas|ibercena|iberescena|ibermedia|ibermuseos|iberoamerican\w*|ibero-americano\w*|"
    r"conselho|comit[eê]s?|eleic\w+|elei[cç][aã]o|licita[cç]\w+|preg[aã]o|sei[ _]\d+)\b",
    re.I,
)
_PDF = re.compile(r"\.pdf(?:$|[?/#])", re.I)


# ── listas ───────────────────────────────────────────────────────────────────────────────────


def parse_links(html: str, url: str, padrao: str) -> list[tuple[str, str]]:
    """Links do miolo cujo TEXTO bate com `padrao` (menu e rodapé do gov.br repetem palavras como "Editais")."""
    pagina = extrair_pagina(html, url)
    rx = re.compile(padrao, re.I)
    visto: dict[str, str] = {}
    for href, texto in pagina.links:
        t = limpar_texto(texto)
        if (
            len(t) >= 12
            and rx.search(t)
            and not _PDF.search(href)
            and href.rstrip("/") != url.rstrip("/")
        ):
            visto.setdefault(href, t)
    return list(visto.items())


_OBJ_VOLTO = re.compile(
    r'\{"@id":"(?P<id>[^"]+)","@type":"(?P<tipo>\w+)"(?P<resto>[^{}]*?)"title":"(?P<titulo>(?:[^"\\]|\\.)*)"'
)


def _desescapar(texto: str) -> str:
    try:
        return (
            bytes(texto, "utf-8")
            .decode("unicode_escape")
            .encode("latin-1", "ignore")
            .decode("utf-8", "ignore")
        )
    except (UnicodeDecodeError, ValueError):
        return texto


def itens_do_estado(html: str) -> list[dict[str, str]]:
    """Objetos `{@id, @type, ..., title}` do estado embutido (`window.__data`), com `\\u002F` já convertido."""
    estado = html.replace("\\u002F", "/")
    saida = []
    for m in _OBJ_VOLTO.finditer(estado):
        resto = m.group("resto")
        desc = re.search(r'"description":"((?:[^"\\]|\\.)*)"', resto)
        eff = re.search(r'"effective":"([^"]+)"', resto)
        saida.append({
            "url": m.group("id"), "tipo": m.group("tipo"), "titulo": limpar_texto(_desescapar(m.group("titulo"))),
            "descricao": limpar_texto(_desescapar(desc.group(1))) if desc else "",
            "publicado_em": eff.group(1) if eff else "",
        })  # fmt: skip
    return saida


def parse_volto(html: str, url_lista: str) -> list[tuple[str, str]]:
    """Filhos diretos da lista (páginas), sem arquivos, sem os "-1" duplicados e sem o próprio menu do site."""
    base = url_lista.rstrip("/") + "/"
    vistos_titulo: set[str] = set()
    saida: list[tuple[str, str]] = []
    for it in itens_do_estado(html):
        if (
            not it["url"].startswith(base)
            or "@" in it["url"]
            or it["tipo"] not in ("Document", "News Item", "Link")
        ):
            continue
        resto = it["url"][len(base) :]
        if "/" in resto or _PDF.search(resto):
            continue
        t = it["titulo"]
        if not t or t.lower() in vistos_titulo:
            continue
        vistos_titulo.add(t.lower())
        saida.append((it["url"], t))
    return saida


def pdfs_do_estado(html: str, url_pagina: str) -> list[tuple[str, str]]:
    """PDFs anexados a uma página Volto (itens filhos tipo Arquivo): [(url para baixar, título)]."""
    base = url_pagina.rstrip("/") + "/"
    saida = []
    for it in itens_do_estado(html):
        if it["tipo"] == "File" and it["url"].startswith(base) and _PDF.search(it["url"]):
            saida.append((it["url"] + "/@@display-file/file", it["titulo"]))
    return saida


# ── detalhe ──────────────────────────────────────────────────────────────────────────────────


def e_fomento(
    titulo: str, url: str, extras: list[str] | None = None, incluir: list[str] | None = None
) -> bool:
    alvo = sem_acento(
        f"{titulo} {urlparse(url).path.rsplit('/', 1)[-1].replace('-', ' ').replace('_', ' ')}"
    )
    if NAO_E_FOMENTO.search(alvo):
        return False
    if incluir and not any(re.search(p, alvo, re.I) for p in incluir):
        return False  # lista de programas misturada com páginas institucionais: só o que parece edital/prêmio/apoio
    return not any(re.search(p, alvo, re.I) for p in (extras or []))


def _escolher_pdfs(pdfs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Primeiro os que parecem o edital em si; depois, regulamento. Anexos, resultados e erratas ficam de fora."""

    def nota(p: tuple[str, str]) -> int:
        t = sem_acento(p[1] + " " + p[0].rsplit("/", 2)[-2] if "/" in p[0] else p[1])
        if re.search(
            r"\b(resultado|errata|retifica\w*|anexo|formulario|ata|habilitacao|inscritos|cronograma)\b",
            t,
        ):
            return 9
        return (
            0
            if re.search(r"\bedital\b", t)
            else 1
            if re.search(r"regulamento|chamada|convocat", t)
            else 2
        )

    return sorted((p for p in pdfs if nota(p) < 9), key=nota)[:MAX_PDFS_POR_EDITAL]


async def _texto_do_edital(
    cliente: ClienteWeb, pagina: Pagina, html: str, url: str, formato: str, intervalo_s: float
) -> tuple[str, str | None]:
    """(texto para achar o prazo, link do PDF do edital). A página primeiro; o PDF só se ela não bastar."""
    texto = pagina.texto
    if achar_prazo(texto):
        pdf = (
            _escolher_pdfs(pdfs_do_estado(html, url) if formato == "volto" else pagina.pdfs)
            or [(None, "")]
        )[0][0]
        return texto, (pdf.removesuffix("/@@display-file/file") if pdf else None)
    escolhidos = _escolher_pdfs(pdfs_do_estado(html, url) if formato == "volto" else pagina.pdfs)
    for link, _ in escolhidos:
        try:
            r = await cliente.obter(link, intervalo_s=intervalo_s)
        except ErroWeb:
            continue
        t = texto_de_pdf(r.conteudo)
        if t and achar_prazo(t):
            return f"{texto}\n{t}", link.removesuffix("/@@display-file/file")
    return texto, (escolhidos[0][0].removesuffix("/@@display-file/file") if escolhidos else None)


def converter(
    url: str, titulo_lista: str, pagina: Pagina, texto: str, pdf: str | None, agora: datetime,
    *, fonte: str, orgao: str, origem: str,
) -> tuple[EditalBruto | None, Candidato | None, str]:  # fmt: skip
    titulo = limpar_texto(pagina.titulo or titulo_lista)
    if len(titulo) < 8 or sem_acento(titulo) in ("abertos", "editais", "editais abertos"):
        titulo = titulo_lista
    prazo = achar_prazo(texto)
    if prazo is None:
        cand = Candidato(
            chave=f"{fonte}:{url}", titulo=titulo, link_oficial=url, origem=origem,
            motivo="a página e o PDF não trazem um prazo de inscrição legível; confira o cronograma no edital",
            resumo=truncar_palavra(_descricao(pagina.texto, titulo), 600) or None,
            campos={"orgao_fomento": orgao, "abrangencia": "nacional"},
        )  # fmt: skip
        return None, cand, "sem_prazo"
    if prazo.fim <= agora:
        return None, None, "encerrado"
    bruto = EditalBruto(
        fonte=fonte, id_externo=url.rstrip("/").rsplit("/", 1)[-1][:120], url_origem=url, url_edital_pdf=pdf,
        titulo=titulo, orgao_fomento=orgao, resumo_original=_descricao(pagina.texto, titulo) or None,
        inscricao_inicio=prazo.inicio, inscricao_fim=prazo.fim, fim_tem_horario=prazo.tem_horario,
        abrangencia="nacional", proveniencia={"inscricao_fim": Proveniencia(via="regex", trecho=prazo.trecho)},
    )  # fmt: skip
    return bruto, None, ""


async def coletar(
    cliente: ClienteWeb,
    f: Any,  # FonteConfig (evita importação circular)
    intervalo_s: float,
    agora: datetime | None = None,
) -> ResultadoFonte:
    agora = agora or datetime.now(UTC)
    res = ResultadoFonte(fonte=f.id)
    formato = f.formato or "cards"
    orgao = f.orgao or f.nome
    origem = f"{orgao} (gov.br), lista de editais"
    itens: list[tuple[str, str]] = []
    for url_lista in f.paginas or [f.url]:
        try:
            lista = await cliente.obter(url_lista, intervalo_s=intervalo_s)
        except ErroWeb as exc:
            res.descartes[f"erro_lista:{type(exc).__name__}"] += 1
            continue
        if formato == "volto":
            itens += parse_volto(lista.texto, url_lista)
        elif formato == "links":
            itens += parse_links(
                lista.texto, lista.url, f.padrao_links or r"edital|chamad|sele[cç]|pr[eê]mio"
            )
        else:
            itens += parse_listagem(lista.texto)
    unicos: dict[str, str] = {}
    for u, t in itens:
        unicos.setdefault(u, t)
    if not unicos:
        res.status = "sem_dados"
        res.detalhe = "nenhum item encontrado: o layout da página pode ter mudado"
        return res
    for url, titulo in list(unicos.items())[: f.max_itens or MAX_ITENS_PADRAO]:
        if not e_fomento(titulo, url, f.excluir_titulo, f.incluir_titulo):
            res.descartes["nao_e_fomento"] += 1
            continue
        try:
            r = await cliente.obter(url, intervalo_s=intervalo_s)
        except ErroWeb as exc:
            res.descartes[f"erro:{type(exc).__name__}"] += 1
            continue
        pagina = extrair_pagina(r.texto, r.url)
        texto, pdf = await _texto_do_edital(cliente, pagina, r.texto, url, formato, intervalo_s)
        bruto, cand, motivo = converter(
            url, titulo, pagina, texto, pdf, agora, fonte=f.id, orgao=orgao, origem=origem
        )
        if bruto:
            res.brutos.append(bruto)
            res.textos[bruto.id_externo] = f"{bruto.titulo} {texto[:20000]}"
        elif cand:
            res.candidatos.append(cand)
            res.descartes[motivo] += 1
        else:
            res.descartes[motivo] += 1
    _ = urljoin
    return res
