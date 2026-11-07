"""Transforma um link oficial desconhecido em `Candidato`, lendo a página (ou o PDF) de origem.

Usado pelas fontes `descoberta` (Radar de Editais) e pelas plataformas que não trazem prazo
(CultBR). Só o que a página oficial diz entra no candidato; nada vem do agregador.
"""

from __future__ import annotations

import re

from .extrator import extrair_pagina, texto_de_pdf
from .fila import Candidato
from .padronizacao import (
    extrair_marco_legal,
    extrair_proponentes,
    limpar_texto,
    resolver_valores,
    truncar_palavra,
)
from .prazos_texto import achar_prazo
from .web import ClienteWeb, ErroWeb


def campos_do_texto(texto: str) -> dict[str, object]:
    """Campos extraídos com regex, cada um com o trecho que o comprova (para conferência)."""
    campos: dict[str, object] = {}
    evidencias: dict[str, str] = {}
    prazo = achar_prazo(texto)
    if prazo:
        campos["inscricao_fim"] = prazo.fim.astimezone().date().isoformat()
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
    """`None` se a página não puder ser lida (bloqueio, erro, robots)."""
    try:
        r = await cliente.obter(link, intervalo_s=intervalo_s)
    except ErroWeb:
        return None
    if "pdf" in r.tipo.lower() or link.lower().split("?")[0].endswith(".pdf"):
        texto = texto_de_pdf(r.conteudo)
        titulo = limpar_texto(titulo_dica or "") or limpar_texto(
            texto.split("\n")[0] if texto else link
        )
    else:
        pagina = extrair_pagina(r.texto, r.url)
        texto = pagina.texto
        titulo = limpar_texto(pagina.titulo or titulo_dica or link)
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
