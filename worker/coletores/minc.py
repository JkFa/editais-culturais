"""Ministério da Cultura: listagem "Editais abertos" (Plone, sem REST nem RSS).

Verificado em 07/11/2026: `++api++` e `/RSS` devolvem 404, então lemos o HTML. A listagem traz só
título e link (`a.govbr-card-content`); cada edital tem página própria com cronograma e PDFs.
A listagem "abertas" também inclui editais já encerrados: o prazo vem da página do edital.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from ..extrator import Pagina, extrair_pagina
from ..fila import Candidato
from ..modelos import EditalBruto, Proveniencia
from ..padronizacao import limpar_texto, truncar_palavra
from ..prazos_texto import achar_prazo
from ..web import ClienteWeb, ErroWeb
from .base import ResultadoFonte

URL_BASE = "https://www.gov.br"
ORGAO = "Ministério da Cultura"
_RE_CARD = re.compile(
    r'<a[^>]+class="[^"]*govbr-card-content[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S
)


def parse_listagem(html: str) -> list[tuple[str, str]]:
    """[(url, título)] dos cards da listagem."""
    vistos: dict[str, str] = {}
    for url, miolo in _RE_CARD.findall(html):
        titulo = limpar_texto(miolo)
        if titulo:
            vistos.setdefault(url, titulo)
    return list(vistos.items())


def _link_inscricao(pagina: Pagina) -> str | None:
    for url, texto in pagina.links:
        if re.search(r"inscri", texto, re.I) and "gov.br/cultura" not in url:
            return url
    return None


def _pdf_do_edital(pagina: Pagina) -> str | None:
    """Documento do edital. O Plone serve arquivos sem extensão, então vale também o texto do link."""
    for url, texto in pagina.links:
        if re.match(r"\s*edital\b", texto, re.I) and "gov.br/cultura" in url:
            return url
    pdfs = pagina.pdfs
    return pdfs[0][0] if pdfs else None


def converter_detalhe(
    url: str,
    titulo_listagem: str,
    html: str,
    agora: datetime,
    fonte: str = "minc-inscricoes-abertas",
) -> tuple[EditalBruto | None, Candidato | None, str]:
    """(edital, candidato, motivo de descarte). Só um dos dois primeiros vem preenchido."""
    pagina = extrair_pagina(html, url)
    titulo = limpar_texto(pagina.titulo or titulo_listagem)
    prazo = achar_prazo(pagina.texto)
    if prazo is None:
        # Sem prazo comprovável não publicamos: vai para a fila, com o link para conferir.
        cand = Candidato(
            chave=f"minc:{url}",
            titulo=titulo,
            link_oficial=url,
            origem="Ministério da Cultura (gov.br/cultura), lista de editais abertos",
            motivo="a página não traz um prazo de inscrição legível; confira o cronograma no edital",
            resumo=truncar_palavra(_descricao(pagina.texto, titulo), 600),
            campos={"orgao_fomento": ORGAO, "abrangencia": "nacional"},
        )
        return None, cand, "sem_prazo"
    if prazo.fim <= agora:
        return None, None, "encerrado"
    id_ext = url.rstrip("/").rsplit("/", 1)[-1]
    descricao = _descricao(pagina.texto, titulo)
    bruto = EditalBruto(
        fonte=fonte,
        id_externo=id_ext,
        url_origem=url,
        url_inscricao=_link_inscricao(pagina),
        url_edital_pdf=_pdf_do_edital(pagina),
        titulo=titulo,
        orgao_fomento=ORGAO,
        resumo_original=descricao,
        areas=[],
        inscricao_inicio=prazo.inicio,
        inscricao_fim=prazo.fim,
        fim_tem_horario=prazo.tem_horario,
        abrangencia="nacional",
        proveniencia={"inscricao_fim": Proveniencia(via="regex", trecho=prazo.trecho)},
    )
    return bruto, None, ""


_RUIDO_GOVBR = re.compile(
    r"^(info|compartilhe.*|link para copiar.*|publicado em|atualizado em|"
    r"\d{2}/\d{2}/\d{4}( \d{1,2}h\d{2})?|anexos?|perguntas frequentes)$",
    re.I,
)


_RODAPE_GOVBR = re.compile(
    r"^(compartilhe:?|ajude a melhorar o gov\.br|rejeitar|fale agora|refazer a busca)$", re.I
)


def _descricao(texto: str, titulo: str) -> str:
    """Texto descritivo para resumo e regex: sem título repetido nem barra de compartilhar/datas.

    Nas páginas do gov.br o miolo começa em "O que é?"; o que vem antes é menu, compartilhamento,
    datas de publicação e links de anexos.
    """
    linhas = [x.strip() for x in texto.split("\n") if x.strip() and x.strip() != titulo]
    inicio = next((i for i, x in enumerate(linhas) if re.fullmatch(r"o que é\??", x, re.I)), None)
    if inicio is not None:
        linhas = linhas[inicio:]
    else:
        linhas = [x for x in linhas if not _RUIDO_GOVBR.match(x)]
    fim = next((i for i, x in enumerate(linhas) if _RODAPE_GOVBR.match(x)), len(linhas))
    return " ".join(linhas[:fim])


async def coletar(
    cliente: ClienteWeb,
    url_listagem: str,
    intervalo_s: float,
    agora: datetime | None = None,
    fonte: str = "minc-inscricoes-abertas",
) -> ResultadoFonte:
    agora = agora or datetime.now(UTC)
    res = ResultadoFonte(fonte=fonte)
    listagem = await cliente.obter(url_listagem, intervalo_s=intervalo_s)
    itens = parse_listagem(listagem.texto)
    if not itens:
        res.status = "sem_dados"
        res.detalhe = "nenhum card encontrado: o layout da página pode ter mudado"
        return res
    for url, titulo in itens:
        try:
            r = await cliente.obter(url, intervalo_s=intervalo_s)
        except ErroWeb as exc:
            res.descartes[f"erro:{type(exc).__name__}"] += 1
            continue
        bruto, cand, motivo = converter_detalhe(url, titulo, r.texto, agora, fonte)
        if bruto:
            res.brutos.append(bruto)
            res.textos[bruto.id_externo] = f"{bruto.titulo} {bruto.resumo_original or ''}"
        elif cand:
            res.candidatos.append(cand)
            res.descartes[motivo] += 1
        else:
            res.descartes[motivo] += 1
    return res
