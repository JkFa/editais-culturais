"""Extração genérica de uma página de edital: título, texto principal, links e PDF.

Só biblioteca padrão (`html.parser`). Ignora scripts, estilos, menus, rodapés e formulários.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

_IGNORAR = {
    "script",
    "style",
    "noscript",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "svg",
    "template",
    "iframe",
}
_BLOCOS = {
    "p",
    "div",
    "li",
    "tr",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "table",
    "ul",
    "ol",
}


@dataclass
class Pagina:
    titulo: str | None = None
    texto: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)  # (url absoluta, texto)
    descricao_meta: str | None = None

    @property
    def pdfs(self) -> list[tuple[str, str]]:
        return [(u, t) for u, t in self.links if re.search(r"\.pdf(\?|$)", u, re.I)]


class _Leitor(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.pilha_ignorar = 0
        self.pedacos: list[str] = []
        self.titulo_tag: list[str] = []
        self.em_titulo = False
        self.h1: list[str] = []
        self.em_h1 = False
        self.og_titulo: str | None = None
        self.meta_desc: str | None = None
        self.links: list[tuple[str, list[str]]] = []
        self._link_atual: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            if a.get("property") == "og:title" and a.get("content"):
                self.og_titulo = a["content"]
            if a.get("name", "").lower() == "description" or a.get("property") == "og:description":
                self.meta_desc = self.meta_desc or (a.get("content") or None)
        if tag == "title":
            self.em_titulo = True
        if tag in _IGNORAR:
            self.pilha_ignorar += 1
            return
        if self.pilha_ignorar:
            return
        if tag == "h1":
            self.em_h1 = True
        if tag == "a" and a.get("href"):
            self._link_atual = []
            self.links.append((urljoin(self.base, a["href"]), self._link_atual))
        if tag in _BLOCOS:
            self.pedacos.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.em_titulo = False
        if tag in _IGNORAR:
            self.pilha_ignorar = max(0, self.pilha_ignorar - 1)
            return
        if tag == "h1":
            self.em_h1 = False
        if tag == "a":
            self._link_atual = None
        if tag in _BLOCOS:
            self.pedacos.append("\n")

    def handle_data(self, data: str) -> None:
        if self.em_titulo:
            self.titulo_tag.append(data)
        if self.pilha_ignorar:
            return
        if self.em_h1:
            self.h1.append(data)
        if self._link_atual is not None:
            self._link_atual.append(data)
        self.pedacos.append(data)


def _limpar(texto: str) -> str:
    linhas = [re.sub(r"[ \t\xa0]+", " ", x).strip() for x in texto.split("\n")]
    return "\n".join(x for x in linhas if x)


def extrair_pagina(html: str, url: str = "") -> Pagina:
    leitor = _Leitor(url)
    leitor.feed(html)
    titulo = (
        _limpar(" ".join(leitor.h1)) or leitor.og_titulo or _limpar(" ".join(leitor.titulo_tag))
    )
    return Pagina(
        titulo=titulo or None,
        texto=_limpar("".join(leitor.pedacos)),
        links=[(u, _limpar(" ".join(t))) for u, t in leitor.links],
        descricao_meta=leitor.meta_desc,
    )


def texto_de_pdf(conteudo: bytes, max_paginas: int = 12) -> str:
    """Texto das primeiras páginas de um PDF. Vazio se não houver `pypdf` ou o PDF for imagem."""
    try:
        from pypdf import PdfReader
    except ImportError:  # dependência opcional
        return ""
    try:
        leitor = PdfReader(io.BytesIO(conteudo))
        partes = [(p.extract_text() or "") for p in leitor.pages[:max_paginas]]
    except Exception:  # noqa: BLE001 - PDF malformado não pode derrubar a coleta
        return ""
    return _limpar("\n".join(partes))
