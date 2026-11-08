"""CultBR (MinC): API pública usada pelo próprio portal (`/v1/public/par/editais`).

Termos de uso (lidos em 07/11/2026): permitem consultar e reproduzir informação pública citando a
fonte e proíbem só "extração automatizada abusiva"; por isso 1 requisição a cada 3 s e páginas de 100.
Itens com `json_oportunidade_mapas` (editais do CultEditais) trazem prazo, valor e proponentes
estruturados e são publicados. Os demais trazem só um link: viram candidatos (no máximo 20 por execução).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..descoberta import candidato_de_link
from ..fila import LIMITE_CANDIDATOS_POR_EXECUCAO, normalizar_url
from ..modelos import EditalBruto, Proveniencia
from ..padronizacao import FUSO_PADRAO, interpretar_fim, interpretar_inicio, limpar_texto
from ..web import ClienteWeb
from .base import ResultadoFonte

log = logging.getLogger("worker.cultbr")

VIA = "CultBR (MinC)"
_PROPONENTES = {
    "pessoa_fisica": "PF",
    "mei_microempreendedor_individual": "MEI",
    "coletivos_e_grupos_informais_sem_cnpj": "COLETIVO",
    "pessoa_juridica_com_fins_lucrativos_empresas": "PJ_COM_FINS",
    "pessoa_juridica_sem_fins_lucrativos": "PJ_SEM_FINS",
}
_ORDEM = ["PF", "MEI", "PJ_COM_FINS", "PJ_SEM_FINS", "COLETIVO"]


def nome_legivel(nome: str) -> str:
    """ "MUNICIPIO DE SAO MIGUEL DE TAIPU" -> "Município de São Miguel de Taipu" (sem inventar acentos)."""
    pequenas = {"de", "da", "do", "dos", "das", "e"}
    palavras = [p.lower() if p.lower() in pequenas else p.capitalize() for p in nome.split()]
    texto = " ".join(palavras)
    return texto.replace("Municipio", "Município")


def _areas_de(segmentos: Any) -> list[str]:
    """A API ora devolve lista, ora um texto ("A, B" ou "Edital não se direciona a ...")."""
    if not segmentos:
        return []
    if isinstance(segmentos, str):
        partes = [x.strip() for x in re.split(r"[,;]", segmentos)]
    else:
        partes = [
            x.strip()
            if isinstance(x, str)
            else str((x or {}).get("label") or (x or {}).get("nome") or "").strip()
            for x in segmentos
        ]
    return [x for x in partes if x and not x.lower().startswith("edital não se direciona")]


def converter(
    item: dict[str, Any], agora: datetime, fonte: str = "cultbr-pnab"
) -> tuple[EditalBruto | None, str]:
    """Item da API com JSON estruturado -> EditalBruto. (None, motivo) se não servir."""
    j = item.get("json_oportunidade_mapas") or {}
    fim = interpretar_fim(j.get("data_final_prazo_inscricao"))
    if not fim:
        return None, "sem_prazo_estruturado"
    inscricao_fim, tem_horario = fim
    if inscricao_fim <= agora:
        return None, "encerrado"
    titulo = limpar_texto(j.get("numero_e_titulo_edital") or item.get("titulo"))
    prov: dict[str, Proveniencia] = {
        "inscricao_fim": Proveniencia(
            via="api", trecho=f"data_final_prazo_inscricao: {j['data_final_prazo_inscricao']}"
        ),
    }
    chaves = [k for k in (j.get("tipos_proponentes") or []) if k in _PROPONENTES]
    proponentes = [p for p in _ORDEM if p in {_PROPONENTES[k] for k in chaves}] or None
    if proponentes:
        prov["proponentes"] = Proveniencia(
            via="api", trecho="tipos_proponentes: " + ", ".join(chaves)
        )
    valor = None
    try:
        bruto_valor = j.get("valor_total_edital")
        valor = Decimal(str(bruto_valor)) if bruto_valor not in (None, "") else None
    except InvalidOperation:
        valor = None
    if valor is not None and valor > 0:
        prov["valor_total"] = Proveniencia(
            via="api", trecho=f"valor_total_edital: {j['valor_total_edital']}"
        )
    else:
        valor = None
    nome_ente = limpar_texto(item.get("nome_ente_recebedor"))
    municipal = nome_ente.upper().startswith("MUNICIPIO")
    uf = item.get("uf_ente_recebedor_plano_acao") or None
    if uf:
        prov["uf"] = Proveniencia(via="api", trecho=f"uf_ente_recebedor_plano_acao: {uf}")
    areas = _areas_de(j.get("segmentos_artistico_culturais"))
    municipio = (
        nome_legivel(limpar_texto(item.get("nome_municipio_ente_recebedor_plano_acao"))) or None
    )
    bruto = EditalBruto(
        fonte=fonte,
        id_externo=str(item["id_par_edital"]),
        url_origem=item["link_inscricao"],
        url_edital_pdf=j.get("pdf_edital"),
        titulo=titulo,
        orgao_fomento=nome_legivel(nome_ente) if nome_ente else "Ente federado (PNAB)",
        resumo_original=limpar_texto(item.get("descricao")),
        areas=areas,
        inscricao_inicio=interpretar_inicio(j.get("data_inicial_prazo_inscricao")),
        inscricao_fim=inscricao_fim,
        fim_tem_horario=tem_horario,
        publicado_em=interpretar_inicio(j.get("data_publicacao_edital")),
        abrangencia="municipal"
        if municipal
        else ("estadual" if nome_ente.upper().startswith("ESTADO") else None),
        uf=uf,
        municipio=municipio if municipal else None,
        proveniencia=prov,
        via=VIA,
        proponentes=proponentes,
        valor_total=valor,
    )
    return bruto, ""


async def coletar(
    cliente: ClienteWeb,
    url_api: str,
    intervalo_s: float,
    urls_conhecidas: set[str],
    marcadores_existentes: set[str],
    agora: datetime | None = None,
    fonte: str = "cultbr-pnab",
) -> ResultadoFonte:
    from ..fila import marcador

    agora = agora or datetime.now(UTC)
    res = ResultadoFonte(fonte=fonte)
    r = await cliente.obter(f"{url_api}?status=Aberto&per_page=100&page=1", intervalo_s=intervalo_s)
    import json

    dados = json.loads(r.texto)
    itens: list[dict[str, Any]] = list(dados.get("items", []))
    for pagina in range(2, int(dados.get("total_pages", 1)) + 1):
        more = await cliente.obter(
            f"{url_api}?status=Aberto&per_page=100&page={pagina}", intervalo_s=intervalo_s
        )
        itens += json.loads(more.texto).get("items", [])
    if not itens:
        res.status = "sem_dados"
        return res
    vistos: set[str] = set()
    for item in itens:
        link = (item.get("link_inscricao") or "").strip()
        chave_url = normalizar_url(link) if link else ""
        if chave_url in vistos:
            res.descartes["duplicado_na_fonte"] += 1
            continue
        vistos.add(chave_url)
        if item.get("json_oportunidade_mapas"):
            bruto, motivo = converter(item, agora, fonte)
            if bruto:
                res.brutos.append(bruto)
                res.textos[bruto.id_externo] = " ".join(
                    x
                    for x in (
                        bruto.titulo,
                        bruto.resumo_original,
                        (item["json_oportunidade_mapas"].get("detalhamento_objeto") or ""),
                    )
                    if x
                )
            else:
                res.descartes[motivo] += 1
            continue
        if not link:
            res.descartes["sem_link"] += 1
            continue
        if chave_url in urls_conhecidas:
            res.descartes["ja_na_base"] += 1
            continue
        chave = f"cultbr:{normalizar_url(link)}"
        if marcador(chave) in marcadores_existentes:
            res.descartes["ja_na_fila"] += 1
            continue
        if len(res.candidatos) >= LIMITE_CANDIDATOS_POR_EXECUCAO:
            res.descartes["limite_de_candidatos"] += 1
            continue
        cand = await candidato_de_link(
            cliente,
            link,
            chave=chave,
            origem="CultBR (MinC): oportunidade aberta sem prazo estruturado",
            titulo_dica=limpar_texto(item.get("titulo")),
            intervalo_s=intervalo_s,
            motivo="o CultBR lista este edital como aberto mas não informa o prazo; confira no link oficial",
        )
        if cand is None:
            res.descartes["pagina_ilegivel"] += 1
            continue
        if (
            cand.encerrado
        ):  # o status "Aberto" do CultBR é defasado: a página/PDF já mostra prazo vencido
            res.descartes["encerrado_na_pagina"] += 1
            continue
        cand.campos.setdefault(
            "orgao_fomento", nome_legivel(limpar_texto(item.get("nome_ente_recebedor")))
        )
        res.candidatos.append(cand)
    _ = FUSO_PADRAO
    return res
