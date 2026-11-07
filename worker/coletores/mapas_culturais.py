"""Coletor de instâncias Mapas Culturais (`/api/opportunity/find`)."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..http import ClienteHttp, ErroColeta
from ..modelos import EditalBruto, Proveniencia
from ..padronizacao import FUSO_PADRAO, interpretar_fim, interpretar_inicio, limpar_texto
from ..taxonomia import sem_acento

log = logging.getLogger("worker.mapas")

CAMPOS = ",".join(
    [
        "id", "name", "shortDescription", "longDescription", "registrationFrom",
        "registrationTo", "type", "terms", "singleUrl", "owner.name", "ownerEntity.name",
        "publishTimestamp", "createTimestamp", "status", "isContinuousFlow", "files",
    ]
)  # fmt: skip
POR_PAGINA = 100
MAX_PAGINAS = 30


@dataclass
class Instancia:
    slug: str
    nome: str
    base_url: str
    abrangencia: str | None = None
    uf: str | None = None
    municipio: str | None = None
    ativa: bool = True
    motivo: str | None = None

    @property
    def fonte(self) -> str:
        return f"mapas:{self.slug}"


@dataclass
class Parametros:
    tipos_aceitos: list[str] = field(default_factory=lambda: ["Edital"])
    excluir_titulo_regex: list[str] = field(default_factory=list)
    ano_sem_prazo_a_partir_de: int = 2090


@dataclass
class ResultadoColeta:
    fonte: str
    brutos: list[EditalBruto] = field(default_factory=list)
    textos: dict[str, str] = field(default_factory=dict)  # id_externo -> texto sem truncar
    descartes: Counter[str] = field(default_factory=Counter)


def _filtro_data(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _url_pdf(arquivos: Any) -> str | None:
    """PDF do edital: o arquivo marcado como "regras" ou, na falta, o primeiro PDF anexado."""
    if not isinstance(arquivos, dict):
        return None
    regras = arquivos.get("rules")
    if isinstance(regras, dict) and "pdf" in (regras.get("mimeType") or ""):
        return regras.get("url")
    for anexo in arquivos.get("downloads") or []:
        if isinstance(anexo, dict) and "pdf" in (anexo.get("mimeType") or ""):
            return anexo.get("url")
    return None


def converter(
    item: dict[str, Any],
    inst: Instancia,
    params: Parametros,
    agora: datetime,
    descartes: Counter[str],
) -> tuple[EditalBruto, str] | None:
    """Item da API -> (EditalBruto, texto completo). `None` se não for um edital válido."""
    tipo = (item.get("type") or {}).get("name")
    if tipo not in params.tipos_aceitos:
        descartes[f"tipo:{tipo}"] += 1
        return None
    titulo = limpar_texto(item.get("name"))
    if not titulo:
        descartes["sem_titulo"] += 1
        return None
    norm = sem_acento(titulo)
    if any(re.search(p, norm) for p in params.excluir_titulo_regex):
        descartes["titulo_excluido"] += 1
        return None
    fim = interpretar_fim(item.get("registrationTo"))
    if fim is None:
        descartes["sem_prazo_final"] += 1
        return None
    inscricao_fim, tem_horario = fim
    if inscricao_fim.astimezone(FUSO_PADRAO).year >= params.ano_sem_prazo_a_partir_de:
        descartes["fluxo_continuo_sem_prazo"] += 1
        return None
    if inscricao_fim <= agora:
        descartes["encerrado"] += 1
        return None
    inicio = interpretar_inicio(item.get("registrationFrom"))
    if inicio and inicio > agora:
        descartes["ainda_nao_abriu"] += 1
        return None

    curta = limpar_texto(item.get("shortDescription"))
    longa = limpar_texto(item.get("longDescription"))
    # A descrição curta é o "resumo"; o texto longo só alimenta a extração (regex/LLM).
    resumo = curta or longa or None
    texto = " ".join(x for x in (titulo, curta, longa) if x)
    id_ext = str(item["id"])
    url = item.get("singleUrl") or f"{inst.base_url}/oportunidade/{id_ext}/"
    dono = (item.get("ownerEntity") or {}).get("name") or (item.get("owner") or {}).get("name")
    publicado = interpretar_inicio(item.get("publishTimestamp")) or interpretar_inicio(
        item.get("createTimestamp")
    )
    prov: dict[str, Proveniencia] = {}
    if inst.uf:
        prov["uf"] = Proveniencia(via="api", trecho=f"Instância {inst.base_url} ({inst.uf})")
    bruto = EditalBruto(
        fonte=inst.fonte,
        id_externo=id_ext,
        url_origem=url,
        url_edital_pdf=_url_pdf(item.get("files")),
        titulo=titulo,
        orgao_fomento=dono or inst.nome,
        resumo_original=resumo,
        areas=list((item.get("terms") or {}).get("area") or []),
        inscricao_inicio=inicio,
        inscricao_fim=inscricao_fim,
        fim_tem_horario=tem_horario,
        publicado_em=publicado,
        abrangencia=inst.abrangencia,  # type: ignore[arg-type]
        uf=inst.uf,
        municipio=inst.municipio,
        proveniencia=prov,
    )
    return bruto, texto


async def coletar_instancia(
    cliente: ClienteHttp, inst: Instancia, params: Parametros, agora: datetime | None = None
) -> ResultadoColeta:
    agora = agora or datetime.now(UTC)
    local = agora.astimezone(FUSO_PADRAO)
    resultado = ResultadoColeta(fonte=inst.fonte)
    consulta = {
        "@select": CAMPOS,
        "@limit": str(POR_PAGINA),
        "@order": "registrationTo ASC",
        # Janela generosa (um dia de folga); o corte exato é refeito no cliente.
        "registrationTo": f"GTE({_filtro_data(local - timedelta(days=1))})",
        "registrationFrom": f"LTE({_filtro_data(local + timedelta(days=1))})",
        "status": "EQ(1)",
    }
    url = f"{inst.base_url.rstrip('/')}/api/opportunity/find"
    for pagina in range(1, MAX_PAGINAS + 1):
        itens = await cliente.json(url, {**consulta, "@page": str(pagina)})
        if not isinstance(itens, list):
            raise ErroColeta(f"{inst.fonte}: resposta inesperada ({type(itens).__name__})")
        for item in itens:
            convertido = converter(item, inst, params, agora, resultado.descartes)
            if convertido:
                bruto, texto = convertido
                resultado.brutos.append(bruto)
                resultado.textos[bruto.id_externo] = texto
        if len(itens) < POR_PAGINA:
            break
    log.info(
        "%s: %d editais, descartes=%s", inst.fonte, len(resultado.brutos), dict(resultado.descartes)
    )
    return resultado
