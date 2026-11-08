"""Transforma um link oficial desconhecido em `Candidato`, lendo a página (ou o PDF) de origem.

Usado pelas fontes `descoberta` (Radar de Editais) e pelas plataformas que não trazem prazo
(CultBR). Só o que a página oficial diz entra no candidato; nada vem do agregador.
"""

from __future__ import annotations

import re

from .extrator import extrair_pagina, texto_de_pdf
from .fila import Candidato
from .padronizacao import (
    FUSO_PADRAO,
    extrair_marco_legal,
    extrair_proponentes,
    limpar_texto,
    resolver_valores,
    truncar_palavra,
)
from .prazos_texto import achar_prazo
from .web import ClienteWeb, ErroWeb

_SINAL_DE_EDITAL = re.compile(
    r"edital|chamamento|chamada\s+p[úu]blica|inscri[çc][ãaõo]|sele[çc][ãa]o|fomento|"
    r"pr[êe]mio|concurso|credenciamento|apoio\s+financeiro|patroc[íi]nio",
    re.I,
)
_TITULO_DE_EDITAL = re.compile(r"edital|chamamento|chamada|concurso|pr[êe]mio|sele[çc][ãa]o", re.I)


def parece_edital(texto: str) -> bool:
    """Página que lê, mas não fala de edital (portal de transparência, "Carregando...") é ruído."""
    return bool(_SINAL_DE_EDITAL.search(texto[:8000]))


def titulo_de_pdf(texto: str, fallback: str) -> str:
    """Primeira linha do PDF que se anuncia como edital; senão, a primeira linha."""
    linhas = [limpar_texto(x) for x in texto.split("\n")[:12] if limpar_texto(x)]
    return next((x for x in linhas if _TITULO_DE_EDITAL.search(x) and len(x) >= 12), None) or (
        linhas[0] if linhas else fallback
    )


def campos_do_texto(texto: str) -> dict[str, object]:
    """Campos extraídos com regex, cada um com o trecho que o comprova (para conferência)."""
    campos: dict[str, object] = {}
    evidencias: dict[str, str] = {}
    prazo = achar_prazo(texto)
    if prazo:
        campos["inscricao_fim"] = prazo.fim.astimezone(FUSO_PADRAO).date().isoformat()
        campos["fim_tem_horario"] = prazo.tem_horario
        evidencias["inscricao_fim"] = prazo.trecho
    for campo, (valor, trecho) in resolver_valores(texto).items():
        campos[campo] = float(valor)
        evidencias[campo] = trecho
    proponentes = extrair_proponentes(texto)
    if proponentes:
        campos["proponentes"] = list(proponentes)
        evidencias["proponentes"] = " | ".join(proponentes.values())
    marco = extrair_marco_legal(texto)
    if marco:
        campos["marco_legal"] = marco[0]
    campos["evidencias"] = evidencias
    return campos


async def candidato_de_link(
    cliente: ClienteWeb,
    link: str,
    *,
    chave: str,
    origem: str,
    titulo_dica: str | None = None,
    intervalo_s: float = 2.0,
    motivo: str = "edital novo fora da base; confira os dados na fonte oficial",
) -> Candidato | None:
    """`None` se a página não puder ser lida (bloqueio, erro, robots) ou não falar de edital."""
    try:
        r = await cliente.obter(link, intervalo_s=intervalo_s)
    except ErroWeb:
        return None
    if "pdf" in r.tipo.lower() or link.lower().split("?")[0].endswith(".pdf"):
        texto = texto_de_pdf(r.conteudo)
        titulo = limpar_texto(titulo_dica or "") or titulo_de_pdf(texto, link)
    else:
        pagina = extrair_pagina(r.texto, r.url)
        texto = pagina.texto
        titulo = limpar_texto(pagina.titulo or titulo_dica or link)
    if texto and not parece_edital(texto):
        return None
    if not texto:
        motivo += " (a página é feita em JavaScript ou o PDF é imagem: o portal não conseguiu ler o texto)"
    campos = campos_do_texto(texto) if texto else {"evidencias": {}}
    return Candidato(
        chave=chave,
        titulo=re.sub(r"\s+", " ", titulo)[:200],
        link_oficial=r.url,
        origem=origem,
        motivo=motivo,
        resumo=truncar_palavra(texto, 600) if texto else None,
        campos=campos,
    )
