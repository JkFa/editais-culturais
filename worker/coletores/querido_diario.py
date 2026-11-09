"""Diários oficiais municipais pelo Querido Diário (Open Knowledge Brasil): descoberta de editais da cauda longa.

Só DESCOBRE: nunca publica direto. Cada edital achado vira **candidato** (Issue) com o trecho que o comprova. Se o próprio texto do
diário citar um link oficial (`.gov.br`...), o candidato aponta para ele e a publicação automática (worker/automatica.py) pode relê-lo e
publicar; se só houver a cópia do diário (espelho do Querido Diário), fica para conferência humana.

Fluxo:
  1. seis consultas por dia (Aldir Blanc, PNAB, chamamento, fomento, prêmio, Lei Paulo Gustavo) na janela dos últimos dias;
  2. classificação do trecho por regras (abertura x ato posterior x ruído de orçamento): sem LLM, de propósito
     (o robots.txt do serviço declara `ai-train=no`, e a cota do Gemini é curta);
  3. para os diários que parecem abertura, lê o texto, acha a passagem do edital e extrai o prazo com o trecho literal;
  4. descarta vencidos, ruído, o que já está no portal e o que já virou candidato.

Respeito: 2 s entre requisições (a documentação pede ritmo baixo; referência de 60 por minuto), nova tentativa com espera nos erros
520/timeout que a API tem, robots.txt consultado, no máximo `MAX_GAZETAS` diários lidos e `MAX_CANDIDATOS` candidatos por execução.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from ..dedup import LIMIAR_TITULO, similaridade_titulos
from ..fila import Candidato, marcador, normalizar_url
from ..modelos import UFS
from ..padronizacao import FUSO_PADRAO, limpar_texto, truncar_palavra
from ..prazos_texto import Prazo, achar_prazo
from ..taxonomia import sem_acento
from ..web import ClienteWeb, ErroWeb
from .base import ResultadoFonte

log = logging.getLogger("worker.querido_diario")

# O serviço mudou de domínio: o endereço novo é o principal; os outros (a documentação ainda cita api.queridodiario.ok.org.br, com SSL quebrado) são reserva.
API_BASES = (
    "https://api.queridodiario.org.br",
    "https://queridodiario.ok.org.br/api",
    "https://api.queridodiario.ok.org.br",
)
TERMOS = [
    '"Aldir Blanc"', "PNAB", '"chamamento público" cultura', '"edital de fomento" cultural',
    '"prêmio" cultural', '"Lei Paulo Gustavo"',
]  # fmt: skip
JANELA_DIAS = 3
POR_PAGINA = 50
MAX_PAGINAS_POR_TERMO = 3
MAX_GAZETAS = 40
MAX_CANDIDATOS = 15
MAX_PASSAGENS_POR_GAZETA = 3
RETENCAO_DIAS = 14
TENTATIVAS = 2

_POSTERIOR = re.compile(
    r"\b(resultado\w*|homologa\w+|extratos?|classifica\w+|retifica\w+|errata|prorroga\w+|republica\w*|convoca\w+|contemplad\w+|"
    r"selecionad\w+|empenho|liquida\w+|dota[cç][aã]o|ata|aprovad\w+|recurso\w*)\b",
    re.I,
)
_ABERTURA = re.compile(
    r"torna p[uú]blic|abertura d[ae]s? inscri|inscri[cç][oõ]es (estar|ser|ficar|poder)|per[ií]odo de inscri|"
    r"edital( de chamamento)?( p[uú]blico)? n|chamamento p[uú]blico n",
    re.I,
)
_PEDIDO_DE_INSCRICAO = re.compile(
    r"torna p[uú]blic|inscri[cç][oõ]es (estar|ser|ficar|poder)|per[ií]odo de inscri|abertura d[ae]s? inscri",
    re.I,
)
_TITULO = re.compile(
    r"((?:EDITAL|Edital)(?:\s+(?:DE|de)\s+(?:CHAMAMENTO|Chamamento)\s+(?:P[ÚU]BLICO|P[úu]blico))?(?:\s+[^\s\d]{1,12}){0,3}?(?:\s+N[ºo°.]*)?\s*\d{1,4}\s*/\s*\d{4}"
    r"|(?:CHAMAMENTO|Chamamento)\s+(?:P[ÚU]BLICO|P[úu]blico)\s*(?:N[ºo°.]*\s*)?\d{1,4}\s*/\s*\d{4})[^.\n]{0,110}"
)
_RUIDO_TITULO = re.compile(
    r"\b(resultado|errata|retifica\w+|homologa\w+|convoca\w+|prorroga\w+|classifica\w+|recurso|extratos?|parecerista\w*|feira|curso|oficinas?|"
    r"vagas?|elei\w+|cadastro|encerrad\w+|suspens\w+|cancelad\w+|anulad\w+|revogad\w+)\b",
    re.I,
)
# "inscrições/inscrição/inscrever", nunca "inscritos no cadastro" (Salvador): só pedido de inscrição vale como prazo.
_INSCRICAO = re.compile(r"inscri[cç](?:[aã]o|[oõ]es)|inscrev", re.I)
# Diário traz de tudo (educação, saúde...): o edital precisa falar de cultura.
_CULTURA = re.compile(
    r"cultur\w*|art[ií]stic\w*|artistas?|aldir blanc|paulo gustavo|\bpnab\b|audiovisual|m[uú]sic\w*|teatr\w*|dan[cç]a|patrim[oô]nio|literat\w*|circo|artes\b|fazedores",
    re.I,
)
_APOIO = re.compile(
    r"R\$\s?\d|\b(premia[cç]|fomento|apoio financeir|bolsa|cach[eê]|sele[cç][aã]o de projetos|termo de execu[cç][aã]o cultural|credenciamento)",
    re.I,
)
_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_SUFIXOS_OFICIAIS = (".gov.br", ".leg.br", ".mp.br", ".jus.br")
_NAO_LINK = re.compile(
    r"forms\.gle|docs\.google|instagram|facebook|youtube|wa\.me|whatsapp|bit\.ly|t\.me|twitter|tiktok",
    re.I,
)


# ── etapa 1: o trecho parece abertura? ───────────────────────────────────────────────────────


def classificar_trecho(texto: str) -> str:
    """'abertura' | 'posterior' | 'ruido' (por regras; o texto completo ainda confirma depois)."""
    t = limpar_texto(texto)
    if _PEDIDO_DE_INSCRICAO.search(t):
        return "abertura"
    if _POSTERIOR.search(t):
        return "posterior"
    return "abertura" if _ABERTURA.search(t) and _TITULO.search(t) else "ruido"


# ── etapa 2: a passagem do edital no diário ──────────────────────────────────────────────────


@dataclass
class Passagem:
    titulo: str
    janela: str
    prazo: Prazo
    link: str | None


def link_oficial(janela: str) -> str | None:
    """Primeiro endereço de site oficial citado na passagem (formulário e rede social não valem)."""
    for m in _URL.finditer(janela):
        url = m.group(0).rstrip(".,;:")
        host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower()
        if _NAO_LINK.search(url) or not host.endswith(_SUFIXOS_OFICIAIS):
            continue
        if url.rstrip("/").count("/") >= 3:  # tem caminho: a home de um site não é o edital
            return url
    return None


def achar_passagens(texto: str, agora: datetime) -> list[Passagem]:
    """Passagens do diário que abrem inscrições de um edital: título, prazo futuro com trecho que fala de "inscri"."""
    saida: list[Passagem] = []
    vistos: set[str] = set()
    for m in _TITULO.finditer(texto):
        titulo = re.sub(r"\s+", " ", m.group(1) + m.group(0)[len(m.group(1)) :]).strip()
        titulo = re.sub(r"\s+(?:Errata|ERRATA|Anexo|ANEXO).*$", "", titulo)[:140].strip()
        chave = re.sub(r"[^a-z0-9]+", "", sem_acento(titulo))[:60]
        if not chave or chave in vistos or _RUIDO_TITULO.search(sem_acento(titulo)):
            continue
        janela = limpar_texto(texto[max(0, m.start() - 200) : m.start() + 9000])
        depois = janela[200:]
        if not _PEDIDO_DE_INSCRICAO.search(depois[:4000]) or not _APOIO.search(depois[:6000]):
            continue
        if not _CULTURA.search(titulo + " " + depois[:3000]):
            continue
        prazo = achar_prazo(depois)
        if not prazo or not _INSCRICAO.search(prazo.trecho) or prazo.fim <= agora:
            continue
        vistos.add(chave)
        saida.append(Passagem(titulo, janela, prazo, link_oficial(depois[:6000])))
        if len(saida) >= MAX_PASSAGENS_POR_GAZETA:
            break
    return saida


# ── consulta à API ───────────────────────────────────────────────────────────────────────────


class ErroApi(Exception):
    """A API do Querido Diário não respondeu (520, timeout, SSL) mesmo depois das novas tentativas."""


async def _json(
    cliente: ClienteWeb, bases: list[str], caminho: str, params: dict[str, Any], intervalo_s: float
) -> Any:
    """GET com nova tentativa para erro passageiro (520, timeout). Erro de conexão/SSL elimina o endereço da lista nesta execução."""
    ultimo: Exception | None = None
    for base in list(bases):
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                r = await cliente.obter(
                    f"{base}{caminho}?{urlencode(params)}", intervalo_s=intervalo_s
                )
                dados = json.loads(r.texto)
                if base != bases[0]:  # o endereço que respondeu passa a ser o primeiro
                    bases.remove(base)
                    bases.insert(0, base)
                return dados
            except (ErroWeb, ValueError) as exc:
                ultimo = exc
                log.warning(
                    "Querido Diário %s (tentativa %d/%d): %s",
                    base,
                    tentativa,
                    TENTATIVAS,
                    str(exc)[:80],
                )
                if "ConnectError" in str(exc) and len(bases) > 1:
                    bases.remove(base)  # SSL/DNS não melhora esperando: o outro endereço assume
                    break
                await asyncio.sleep(3 * tentativa)
    raise ErroApi(str(ultimo)[:200])


def _uf_e_municipio(g: dict[str, Any]) -> tuple[str | None, str]:
    uf = (g.get("state_code") or "").upper()
    return (uf if uf in UFS else None), limpar_texto(g.get("territory_name") or "")


def ja_no_portal(
    existentes: list[Any] | None,
    uf: str | None,
    municipio: str,
    prazo: Prazo,
    titulo: str,
    link: str | None,
    urls: set[str],
) -> bool:
    """O edital já está no portal (Mapas, CultBR...): mesmo endereço, ou mesma UF e mesmo dia de prazo com o município no
    título/órgão (títulos de prefeitura são genéricos: "Edital de Chamamento Público nº 02/2026") ou título parecido."""
    if link and normalizar_url(link) in urls:
        return True
    dia = prazo.fim.astimezone(FUSO_PADRAO).date()
    cidade = re.sub(r"[^a-z0-9]+", "", sem_acento(municipio))
    for e in existentes or []:
        if e.uf != uf or e.inscricao_fim.astimezone(FUSO_PADRAO).date() != dia:
            continue
        alvo = re.sub(
            r"[^a-z0-9]+", "", sem_acento(f"{e.titulo} {e.orgao_fomento} {e.municipio or ''}")
        )
        if (len(cidade) >= 4 and cidade in alvo) or similaridade_titulos(
            titulo, e.titulo
        ) >= LIMIAR_TITULO:
            return True
    return False


def candidato_da_passagem(g: dict[str, Any], p: Passagem) -> Candidato:
    uf, municipio = _uf_e_municipio(g)
    evidencias = {"inscricao_fim": p.prazo.trecho}
    campos: dict[str, Any] = {
        "orgao_fomento": f"Município de {municipio}", "abrangencia": "municipal", "uf": uf, "municipio": municipio,
        "inscricao_fim": p.prazo.fim.astimezone(FUSO_PADRAO).date().isoformat(), "fim_tem_horario": p.prazo.tem_horario,
        "evidencias": evidencias,
    }  # fmt: skip
    from ..descoberta import (
        campos_do_texto,
    )  # valor, proponentes e marco legal com trecho, pelas mesmas regras do resto

    extra = campos_do_texto(p.janela[200:])
    for k in ("valor_total", "teto_por_projeto", "proponentes", "marco_legal"):
        if k in extra:
            campos[k] = extra[k]
    evidencias.update(
        {k: v for k, v in (extra.get("evidencias") or {}).items() if k != "inscricao_fim"}
    )
    chave = (
        f"qd:{g['territory_id']}:{g['date']}:{re.sub(r'[^a-z0-9]+', '', sem_acento(p.titulo))[:50]}"
    )
    ref = p.link or g.get("url") or g.get("txt_url")
    return Candidato(
        chave=chave,
        titulo=p.titulo,
        link_oficial=ref,
        origem=f"Diário Oficial de {municipio} ({uf or '?'}), de {g['date']}, via Querido Diário",
        motivo=(
            "o edital foi publicado no diário oficial do município e cita o link acima"
            if p.link
            else "o edital foi lido no diário oficial do município; o link é a cópia do diário (Querido Diário). Procure o edital no site da prefeitura"
        ),
        resumo=truncar_palavra(p.janela[200:], 600),
        campos=campos,
    )


async def coletar(
    cliente: ClienteWeb,
    f: Any,  # FonteConfig
    intervalo_s: float,
    agora: datetime | None = None,
    urls_conhecidas: set[str] | None = None,
    marcadores: set[str] | None = None,
    estado: dict[str, Any] | None = None,
    existentes: list[Any] | None = None,
) -> ResultadoFonte:
    agora = agora or datetime.now(UTC)
    res = ResultadoFonte(fonte=f.id)
    urls, marcadores = urls_conhecidas or set(), marcadores or set()
    processadas: dict[str, str] = {
        k: v
        for k, v in ((estado or {}).get("gazetas") or {}).items()
        if v >= (agora.date() - timedelta(days=RETENCAO_DIAS)).isoformat()
    }
    bases = [f.url.rstrip("/")] + [b for b in API_BASES if b != f.url.rstrip("/")]
    desde = (agora.astimezone(FUSO_PADRAO).date() - timedelta(days=JANELA_DIAS)).isoformat()

    # 1) consultas: gazetas únicas, com os trechos de todos os termos que bateram
    achadas: dict[str, dict[str, Any]] = {}
    falhas = 0
    for termo in TERMOS:
        for pagina in range(MAX_PAGINAS_POR_TERMO):
            params = {"querystring": termo, "published_since": desde, "size": POR_PAGINA, "offset": pagina * POR_PAGINA,
                      "excerpt_size": 700, "number_of_excerpts": 3, "sort_by": "descending_date"}  # fmt: skip
            try:
                dados = await _json(cliente, bases, "/gazettes", params, intervalo_s)
            except ErroApi as exc:
                falhas += 1
                res.descartes["erro_consulta"] += 1
                log.error("consulta %s falhou: %s", termo, exc)
                break
            lote = dados.get("gazettes") or []
            for g in lote:
                alvo = achadas.setdefault(g["txt_url"], {**g, "excerpts": []})
                alvo["excerpts"] += [
                    e for e in (g.get("excerpts") or []) if e not in alvo["excerpts"]
                ]
            if len(lote) < POR_PAGINA:
                break
    if falhas == len(TERMOS):
        raise ErroApi("nenhuma consulta respondeu: a API do Querido Diário está fora do ar")
    res.estado["total_links"] = len(achadas)

    # 2) trecho parece abertura? (só os diários novos, mais recentes primeiro)
    novos = [g for g in achadas.values() if g["txt_url"] not in processadas]
    res.descartes["ja_processada"] += len(achadas) - len(novos)

    # Sem sinal nenhum (orçamento, ata): descarta. Com sinal de abertura lê primeiro; os "posteriores" só se sobrar orçamento de leitura.
    def nota(g: dict[str, Any]) -> int:
        return max(
            (2 if (c := classificar_trecho(e)) == "abertura" else 1 if c == "posterior" else 0)
            for e in g["excerpts"] or [""]
        )

    ordenados = sorted(novos, key=lambda g: (nota(g), g["date"]), reverse=True)
    triagem = [g for g in ordenados if nota(g) > 0]
    res.descartes["trecho_sem_sinal_de_edital"] += len(novos) - len(triagem)

    # 3) lê o diário e acha a passagem do edital
    encontrados: list[tuple[dict[str, Any], Passagem]] = []
    for g in triagem[:MAX_GAZETAS]:
        try:
            r = await cliente.obter(g["txt_url"], intervalo_s=intervalo_s)
        except ErroWeb as exc:
            res.descartes[f"erro_diario:{type(exc).__name__}"] += 1
            continue
        processadas[g["txt_url"]] = g["date"]
        passagens = achar_passagens(r.texto, agora)
        if not passagens:
            res.descartes["sem_passagem_de_abertura"] += 1
        encontrados += [(g, p) for p in passagens]
    res.descartes["limite_de_diarios"] += max(0, len(triagem) - MAX_GAZETAS)

    # 4) candidatos: o que não está no portal nem na fila, os de prazo mais próximo primeiro
    for g, p in sorted(encontrados, key=lambda gp: gp[1].prazo.fim):
        _, municipio = _uf_e_municipio(g)
        if not municipio:
            res.descartes["sem_municipio"] += 1
            continue
        cand = candidato_da_passagem(g, p)
        if marcador(cand.chave) in marcadores:
            res.descartes["ja_na_fila"] += 1
        elif ja_no_portal(
            existentes, cand.campos["uf"], municipio, p.prazo, p.titulo, p.link, urls
        ):
            res.descartes["ja_no_portal"] += 1
        elif len(res.candidatos) >= MAX_CANDIDATOS:
            res.descartes["limite_de_candidatos"] += 1
        else:
            res.candidatos.append(cand)
    res.estado["gazetas"] = processadas
    log.info(
        "querido-diario: %d diários, %d lidos, %d candidatos, descartes=%s",
        len(achadas),
        len(triagem[:MAX_GAZETAS]),
        len(res.candidatos),
        dict(res.descartes),
    )
    _ = date
    return res
