"""Obter o documento do edital (PDF ou página), uma vez só, com hash, e extrair o texto por página."""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from worker.extrator import extrair_pagina
from worker.web import ClienteWeb, ErroWeb

MIN_CARACTERES_PDF = (
    400  # abaixo disso o PDF é escaneado (sem camada de texto) e não há OCR nesta fase
)


class SemDocumento(Exception):
    """Não há documento utilizável (sem URL, bloqueado, grande demais ou PDF escaneado)."""


@dataclass
class Documento:
    url: str
    sha256: str
    tipo: str  # "pdf" | "html"
    paginas: list[str] = field(default_factory=list)  # uma string por página (HTML: uma só)

    @property
    def texto(self) -> str:
        return "\n".join(self.paginas)


def hash_dos_bytes(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def paginas_de_pdf(dados: bytes) -> list[str]:
    """Texto de cada página. `pdftotext` (poppler) quando existe; senão `pypdf`."""
    if shutil.which("pdftotext"):
        r = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", "-", "-"], input=dados, capture_output=True, timeout=120
        )
        if r.returncode == 0:
            paginas = r.stdout.decode("utf-8", errors="replace").split("\f")
            if paginas and not paginas[-1].strip():
                paginas.pop()  # o último \f não abre página nova
            return paginas
    from pypdf import PdfReader  # noqa: PLC0415 (dependência já usada pelo worker)

    leitor = PdfReader(io.BytesIO(dados))
    return [(p.extract_text() or "") for p in leitor.pages]


def documento_de_bytes(url: str, dados: bytes, tipo_http: str = "") -> Documento:
    """Monta o Documento a partir dos bytes baixados. Levanta `SemDocumento` se não houver texto."""
    sha = hash_dos_bytes(dados)
    eh_pdf = (
        dados[:5] == b"%PDF-"
        or "pdf" in tipo_http.lower()
        or url.lower().split("?")[0].endswith(".pdf")
    )
    if eh_pdf:
        paginas = paginas_de_pdf(dados)
        total = sum(len(re.sub(r"\s+", "", p)) for p in paginas)
        if total < MIN_CARACTERES_PDF:
            raise SemDocumento(
                "PDF sem camada de texto (provavelmente escaneado); não há OCR nesta fase"
            )
        return Documento(url=url, sha256=sha, tipo="pdf", paginas=paginas)
    texto = extrair_pagina(dados.decode("utf-8", errors="replace"), url).texto
    if len(re.sub(r"\s+", "", texto)) < MIN_CARACTERES_PDF:
        raise SemDocumento(
            "a página não traz texto suficiente (provavelmente montada por JavaScript)"
        )
    return Documento(url=url, sha256=sha, tipo="html", paginas=[texto])


async def baixar_documento(
    cliente: ClienteWeb, url: str | None, intervalo_s: float = 3.0
) -> Documento:
    """Baixa UMA vez (robots.txt, User-Agent do portal e intervalo respeitados pelo `ClienteWeb`)."""
    if not url:
        raise SemDocumento("o edital não tem link de documento")
    try:
        r = await cliente.obter(url, intervalo_s=intervalo_s)
    except ErroWeb as exc:
        if "grande demais" in str(exc):
            raise SemDocumento(str(exc)) from exc
        raise
    return documento_de_bytes(r.url, r.conteudo, r.tipo)
