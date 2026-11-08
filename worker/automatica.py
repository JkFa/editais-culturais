"""Publicação automática de candidatos (MODO SOMBRA): decide o que o portal poderia publicar sem aprovação humana.

Por enquanto esta etapa só LÊ e RELATA: nenhum edital é publicado. Um candidato só seria publicado se TODAS as regras
valerem (qualquer dúvida = continua na fila humana):

 1. a Issue está aberta e não tem o rótulo `nao-publicar` (o veto humano);
 2. o link é de domínio oficial: terminação `.gov.br`, `.leg.br`, `.mp.br`, `.jus.br`, ou está em
    `worker/referencia/dominios_oficiais.json` (lista aprovada pelo dono do projeto);
 3. a página/PDF é lida de novo agora (respeitando robots.txt) e tem texto;
 4. o prazo de inscrição existe no texto, é futuro (até `PRAZO_MAX_DIAS` à frente) e o trecho que o comprova está
    literalmente no texto e fala de "inscri...";
 5. é fomento ou seleção (sinais de apoio no texto e nenhum sinal de ruído ou de ato posterior no título);
 6. o órgão aparece literalmente no início do documento (ou vem da lista de domínios aprovados);
 7. a UF vem da terminação do domínio ou da lista aprovada;
 8. no máximo `TETO_POR_EXECUCAO` por execução.

Nada é inferido sem trecho: o resultado traz `evidencias` (campo -> trecho literal) como a curadoria exige.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from .coletores.curadoria import EditalCurado
from .extrator import extrair_pagina, texto_de_pdf
from .fila import ROTULO_AUTO, ROTULO_VETO, FilaGitHub
from .modelos import UFS, EditalBruto, Proveniencia
from .padronizacao import (
    FUSO_PADRAO,
    extrair_proponentes,
    limpar_texto,
    resolver_valores,
    truncar_palavra,
)
from .prazos_texto import achar_prazo
from .taxonomia import sem_acento
from .web import ClienteWeb, ErroWeb

log = logging.getLogger("worker.automatica")
RAIZ = Path(__file__).resolve().parent
DOMINIOS = RAIZ / "referencia" / "dominios_oficiais.json"
FONTE_AUTOMATICA = "publicacao-automatica"
SUFIXOS_OFICIAIS = (".gov.br", ".leg.br", ".mp.br", ".jus.br")
TETO_POR_EXECUCAO = 15
PRAZO_MAX_DIAS = 240  # prazo mais de 8 meses à frente costuma ser ano errado ou fluxo contínuo

_ABERTURA = re.compile(
    r"\b(edital|chamamento|chamada p[uú]blica|sele[cç][aã]o|premia[cç][aã]o|pr[eê]mio|fomento|credenciamento|bolsa|resid[eê]ncia|concurso)\b",
    re.I,
)
_APOIO = re.compile(
    r"R\$\s?\d|\b(premia[cç]|fomento|apoio financeir|bolsa|cach[eê]|contrata[cç][aã]o de|sele[cç][aã]o de projetos|credenciamento de artistas|aporte)",
    re.I,
)
# Atos posteriores ou ruído, olhando SÓ o título (palavras como "oficina" aparecem no corpo de editais legítimos).
_RUIDO_NO_TITULO = re.compile(
    r"\b(resultado|errata|retifica[cç][aã]o|homologa[cç][aã]o|convoca[cç][aã]o|prorroga[cç][aã]o|classifica[cç][aã]o|recurso|"
    r"feira|expositor\w*|lan[cç]amentos? de livros|curso|oficinas?|aulas?|vagas?|elei[cç][aã]o|palestra|cadastro|"
    r"ficha|formul[aá]rio|presta[cç][aã]o de contas|parecerista\w*|extrato|ata|encerrad\w+|suspens\w+|cancelad\w+|anulad\w+|revogad\w+)\b",
    re.I,
)
_STOP_ORGAO = {
    "edital", "chamamento", "processo", "secretaria", "cnpj", "pnab", "lei", "pol", "politica", "política", "comissão",
    "comissao", "cultura", "cultural", "fundo", "conselho", "departamento", "diretoria", "torna", "público", "publico",
    "n", "nº", "no", "ano", "anexo", "i", "ii", "iii", "iv", "paulo", "gustavo",
}  # fmt: skip
_CONECTORES = {"de", "do", "da", "dos", "das", "e"}
_PREFIXOS_ORGAO = re.compile(
    r"\b(prefeitura municipal|prefeitura|munic[ií]pio|governo do estado|secretaria municipal|secretaria de estado|"
    r"secretaria estadual|funda[cç][aã]o|instituto)\b\s+(?:d[eoa]s?\s+)?",
    re.I,
)


# ── domínios oficiais ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DominioAprovado:
    host: str
    orgao: str
    abrangencia: str | None = None
    uf: str | None = None


def carregar_dominios(caminho: Path = DOMINIOS) -> dict[str, DominioAprovado]:
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        d["host"].lower(): DominioAprovado(
            d["host"].lower(), d["orgao"], d.get("abrangencia"), d.get("uf")
        )
        for d in dados.get("dominios", [])
    }


def host_de(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def achar_dominio(host: str, aprovados: dict[str, DominioAprovado]) -> DominioAprovado | None:
    """O domínio aprovado cobre o próprio host e seus subdomínios."""
    partes = host.split(".")
    for i in range(len(partes) - 1):
        d = aprovados.get(".".join(partes[i:]))
        if d:
            return d
    return None


def eh_oficial(host: str, aprovados: dict[str, DominioAprovado]) -> str | None:
    """'sufixo' | 'lista' | None."""
    if f".{host}".endswith(SUFIXOS_OFICIAIS):
        return "sufixo"
    return "lista" if achar_dominio(host, aprovados) else None


def uf_do_host(host: str) -> str | None:
    m = re.search(r"\.([a-z]{2})\.(?:gov|leg)\.br$", host)
    return m.group(1).upper() if m and m.group(1).upper() in UFS else None


# ── extração com evidência ───────────────────────────────────────────────────────────────────


def normalizar(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", sem_acento(texto or "")).strip()


def trecho_no_texto(trecho: str, texto: str) -> bool:
    t = normalizar(trecho)
    return len(t) >= 12 and t in normalizar(texto)


def _legivel(nome: str) -> str:
    """Maiúsculas viram "Título" (sem inventar acentos); texto já misto é mantido."""
    if nome.isupper():
        pequenas = {"de", "do", "da", "dos", "das", "e"}
        return " ".join(
            p.lower() if p.lower() in pequenas else p.capitalize() for p in nome.split()
        )
    return nome


def achar_orgao(texto: str) -> tuple[str, str] | None:
    """(nome do órgão, trecho literal) lido do início do documento; None se não achar com segurança."""
    inicio = limpar_texto(texto[:2500])
    for m in _PREFIXOS_ORGAO.finditer(inicio):
        nome: list[str] = []
        for p in inicio[m.end() :].split()[:7]:
            limpo = p.strip(",.;:()-–—/")
            if limpo.lower() in _CONECTORES:
                nome.append(limpo)
                continue
            if not limpo or limpo.lower() in _STOP_ORGAO or not limpo[0].isupper():
                break
            nome.append(limpo)
        while nome and nome[-1].lower() in _CONECTORES:
            nome.pop()
        if not any(p.lower() not in _CONECTORES for p in nome):
            continue
        literal = re.sub(r"\s+", " ", f"{inicio[m.start() : m.end()]}{' '.join(nome)}").strip()
        return _legivel(literal), literal
    return None


def abrangencia_do_orgao(orgao: str) -> str | None:
    o = sem_acento(orgao)
    if o.startswith(("prefeitura", "municipio")):
        return "municipal"
    if o.startswith(("governo do estado", "secretaria de estado", "secretaria estadual")):
        return "estadual"
    return None


_GENERICOS_HOST = {"www", "gov", "br", "leg", "mp", "jus"}


def conferir_municipio(orgao: str, host: str) -> str | None:
    """Órgão municipal só vale se o NOME DA CIDADE aparece no domínio (smc.niteroi.rj.gov.br -> Niterói).

    Isso derruba "Governo Federal" de cabeçalho e corta lixo de menu ("Município de Piracicaba Cidadão Empresa...").
    Devolve o órgão aparado até onde o nome bate com o domínio, ou None se não bate.
    """
    m = _PREFIXOS_ORGAO.match(orgao.strip())
    if not m:
        return None
    prefixo, nome = orgao[: m.end()].strip(), orgao[m.end() :].split()
    labels = [
        normalizar(x).replace(" ", "")
        for x in host.split(".")
        if x not in _GENERICOS_HOST and len(x) > 2
    ]
    melhor = 0
    for k in range(1, len(nome) + 1):
        cand = normalizar(" ".join(nome[:k])).replace(" ", "")
        if any(cand and cand in lab for lab in labels):
            melhor = k
        else:
            break
    while melhor and nome[melhor - 1].lower() in _CONECTORES:
        melhor -= 1
    cidade = normalizar(" ".join(nome[:melhor])).replace(" ", "")
    if not melhor or len(cidade) < 4:
        return None
    return f"{prefixo} {' '.join(nome[:melhor])}"


_TITULO_BOM = re.compile(
    r"edital|chamamento|chamada|concurso|pr[eê]mio|sele[cç][aã]o|credenciamento", re.I
)
_TITULO_RUIM = re.compile(r"confira|abre inscri|fale conosco|\.\.\.|clique|saiba mais", re.I)
_TITULO_NO_TEXTO = re.compile(
    r"((?:EDITAL|Edital|CHAMAMENTO P[ÚU]BLICO|Chamamento P[úu]blico)(?:\s+[^\s\d]{1,25}){0,5}?(?:\s+N[ºo°.]*)?\s*\d{1,4}(?:\s*/\s*\d{4})?[^.]{0,110})"
)
_CORTE_TITULO = re.compile(r"\s+(?:Errata|ERRATA|Anexo|ANEXO|Retifica\w*|RETIFICA\w*)\b.*$")


def _cortar(t: str, n: int) -> str:
    """Corta em fim de palavra e tira o número de item solto no fim ("... PROJETOS 1.")."""
    if len(t) > n:
        t = t[:n].rsplit(" ", 1)[0]
    return re.sub(r"[\s,;:–-]+\d{1,2}\.?$", "", t).strip()


def melhor_titulo(titulo_issue: str, texto: str) -> str | None:
    """Título do Issue se parecer título de edital; senão, o primeiro "EDITAL Nº x/AAAA ..." do início do documento."""
    t = _CORTE_TITULO.sub("", re.sub(r"\s+", " ", titulo_issue or "").strip())
    if len(t) >= 15 and _TITULO_BOM.search(t) and not _TITULO_RUIM.search(t):
        return _cortar(t, 200)
    m = _TITULO_NO_TEXTO.search(limpar_texto(texto[:3000]))
    if not m:
        return None
    return _cortar(_CORTE_TITULO.sub("", re.sub(r"\s+", " ", m.group(1)).strip()), 120)


# ── decisão ──────────────────────────────────────────────────────────────────────────────────


@dataclass
class Decisao:
    numero: int
    titulo: str
    link: str
    publica: bool = False
    motivos: list[str] = field(default_factory=list)  # por que NÃO publica (vazio = pode publicar)
    campos: dict[str, Any] = field(default_factory=dict)
    evidencias: dict[str, str] = field(default_factory=dict)

    def barrar(self, motivo: str) -> Decisao:
        self.motivos.append(motivo)
        return self


def avaliar(
    numero: int,
    titulo: str,
    link: str,
    texto: str | None,
    hoje: datetime,
    aprovados: dict[str, DominioAprovado],
    *,
    rotulos: set[str] | None = None,
    orgao_estruturado: tuple[str, str] | None = None,
) -> Decisao:
    """Aplica as regras 1 a 7 a UM candidato. Função pura: o texto já foi lido."""
    d = Decisao(numero, titulo, link)
    if ROTULO_VETO in (rotulos or set()):
        return d.barrar("veto humano (rótulo nao-publicar)")
    host = host_de(link)
    origem = eh_oficial(host, aprovados)
    if not origem:
        return d.barrar(f"domínio não oficial ({host})")
    if not texto or len(texto) < 200:
        return d.barrar("página sem texto legível (JavaScript, imagem ou bloqueio)")
    if _RUIDO_NO_TITULO.search(sem_acento(titulo)):
        return d.barrar("título de ruído ou de ato posterior (resultado, errata, feira, curso...)")
    if not _ABERTURA.search(sem_acento(titulo + " " + texto[:600])) or not _APOIO.search(
        texto[:8000]
    ):
        return d.barrar("não parece edital de fomento ou seleção")
    prazo = achar_prazo(texto)
    if not prazo:
        return d.barrar("prazo de inscrição não encontrado no texto")
    if not trecho_no_texto(prazo.trecho, texto) or "inscri" not in sem_acento(prazo.trecho):
        return d.barrar("trecho do prazo não verificável ou não fala de inscrições")
    if prazo.fim <= hoje:
        return d.barrar(f"prazo já passou ({prazo.fim.date()})")
    if prazo.fim > hoje + timedelta(days=PRAZO_MAX_DIAS):
        return d.barrar(f"prazo muito distante ({prazo.fim.date()}): possível ano errado")

    aprov = achar_dominio(host, aprovados)
    orgao = orgao_estruturado
    abrangencia = aprov.abrangencia if aprov else None
    uf = (aprov.uf if aprov else None) or uf_do_host(host)
    if aprov:
        orgao = orgao or (aprov.orgao, f"domínio aprovado: {aprov.host}")
    orgao = orgao or achar_orgao(texto)
    if not orgao:
        return d.barrar("órgão não encontrado no início do documento")
    if not trecho_no_texto(orgao[1], texto) and not orgao[1].startswith(
        ("domínio aprovado", "CultBR")
    ):
        return d.barrar("trecho do órgão não verificável")
    abrangencia = abrangencia or abrangencia_do_orgao(orgao[0])
    if abrangencia is None:
        return d.barrar(
            "órgão sem abrangência reconhecida (só prefeitura/município e governo do estado)"
        )
    if origem == "sufixo" and abrangencia == "municipal":
        conferido = conferir_municipio(orgao[0], host)
        if not conferido:
            return d.barrar(f"o nome do órgão ({orgao[0]}) não confere com o domínio ({host})")
        orgao = (conferido, orgao[1])
    nome_edital = melhor_titulo(titulo, texto)
    if not nome_edital:
        return d.barrar("título do edital não identificado")
    if _RUIDO_NO_TITULO.search(sem_acento(nome_edital)):
        return d.barrar("o título do edital indica encerramento, suspensão ou ato posterior")
    if not uf and abrangencia != "nacional":
        return d.barrar("UF não identificada (nem pelo domínio nem pela lista aprovada)")

    d.campos = {
        "titulo": nome_edital,
        "orgao_fomento": orgao[0],
        "url_origem": link,
        "resumo_simples": truncar_palavra(texto, 280),
        "linguagens": ["Multiáreas"],
        "abrangencia": abrangencia,
        "uf": uf,
        "inscricao_fim": prazo.fim.astimezone(FUSO_PADRAO).date().isoformat(),
        "fim_tem_horario": prazo.tem_horario,
        "origem_dominio": origem,
    }
    d.evidencias = {"inscricao_fim": prazo.trecho, "orgao_fomento": orgao[1]}
    for campo, (valor, trecho) in resolver_valores(texto).items():
        d.campos[campo] = float(valor)
        d.evidencias[campo] = trecho
    proponentes = extrair_proponentes(texto)
    if proponentes:
        d.campos["proponentes"] = list(proponentes)
        d.evidencias["proponentes"] = " | ".join(proponentes.values())
    d.publica = True
    return d


def aplicar_teto(decisoes: list[Decisao], teto: int = TETO_POR_EXECUCAO) -> None:
    """Regra 8: só os `teto` de prazo mais próximo; o excedente fica na fila humana."""
    candidatas = sorted((d for d in decisoes if d.publica), key=lambda d: d.campos["inscricao_fim"])
    for d in candidatas[teto:]:
        d.publica = False
        d.motivos.append(f"acima do teto de {teto} por execução")


# ── leitura das Issues e relatório ───────────────────────────────────────────────────────────


def link_da_issue(corpo: str) -> str | None:
    m = re.search(r"\*\*Link oficial:\*\*\s*(\S+)", corpo or "")
    return m.group(1) if m else None


def titulo_da_issue(titulo: str) -> str:
    return re.sub(r"^Candidato:\s*", "", titulo or "").strip()


def orgao_da_issue(corpo: str) -> tuple[str, str] | None:
    """`orgao_fomento` que o CultBR já trouxe estruturado (vem no YAML proposto)."""
    m = re.search(r"```ya?ml\n(.*?)```", corpo or "", re.S)
    if not m:
        return None
    try:
        y = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    nome = y.get("orgao_fomento")
    return (str(nome), f"CultBR: nome_ente_recebedor = {nome}") if nome else None


async def ler_texto(cliente: ClienteWeb, link: str) -> tuple[str | None, str | None]:
    """(texto, motivo de falha)."""
    try:
        r = await cliente.obter(link, intervalo_s=2.0)
    except ErroWeb as exc:
        return None, f"não foi possível ler a página ({type(exc).__name__})"
    if "pdf" in r.tipo.lower() or link.lower().split("?")[0].endswith(".pdf"):
        return texto_de_pdf(r.conteudo), None
    return extrair_pagina(r.texto, r.url).texto, None


async def avaliar_candidatos(
    issues: list[dict[str, Any]],
    cliente: ClienteWeb,
    aprovados: dict[str, DominioAprovado],
    hoje: datetime,
) -> list[Decisao]:
    decisoes: list[Decisao] = []
    for issue in issues:
        n, corpo = int(issue["number"]), issue.get("body") or ""
        titulo = titulo_da_issue(issue.get("title", ""))
        link = link_da_issue(corpo)
        rotulos = {(x["name"] if isinstance(x, dict) else str(x)) for x in issue.get("labels", [])}
        if not link:
            decisoes.append(Decisao(n, titulo, "", motivos=["Issue sem link oficial"]))
            continue
        if ROTULO_VETO in rotulos or not eh_oficial(host_de(link), aprovados):
            decisoes.append(avaliar(n, titulo, link, None, hoje, aprovados, rotulos=rotulos))
            continue  # nem lê a página: já está barrado (poupa requisições)
        texto, falha = await ler_texto(cliente, link)
        if falha:
            decisoes.append(Decisao(n, titulo, link, motivos=[falha]))
            continue
        decisoes.append(
            avaliar(
                n,
                titulo,
                link,
                texto,
                hoje,
                aprovados,
                rotulos=rotulos,
                orgao_estruturado=orgao_da_issue(corpo),
            )
        )
    aplicar_teto(decisoes)
    return decisoes


def relatorio_md(decisoes: list[Decisao], hoje: datetime) -> str:
    pubs = [d for d in decisoes if d.publica]
    linhas = [
        f"## Publicação automática: modo sombra ({hoje.date()})",
        "",
        f"**{len(pubs)} de {len(decisoes)}** candidatos seriam publicados. **Nada foi publicado.**",
        "",
    ]
    if pubs:
        linhas += [
            "### Publicaria",
            "",
            "| # | Edital | Órgão | UF | Prazo | Trecho do prazo |",
            "|---|---|---|---|---|---|",
        ]
        for d in sorted(pubs, key=lambda x: x.campos["inscricao_fim"]):
            c = d.campos
            linhas.append(
                f"| #{d.numero} | [{c['titulo'][:70]}]({d.link}) | {c['orgao_fomento']} | {c['uf'] or '—'} | {c['inscricao_fim']} | "
                f"{d.evidencias['inscricao_fim'][:90].replace('|', '/')} |"
            )
        linhas.append("")
    por_motivo: dict[str, list[Decisao]] = {}
    for d in decisoes:
        if not d.publica:
            por_motivo.setdefault(d.motivos[0] if d.motivos else "?", []).append(d)
    linhas += [
        "### Não publicaria (por motivo)",
        "",
        "| Motivo | Qtd | Exemplos |",
        "|---|---|---|",
    ]
    for motivo, lista in sorted(por_motivo.items(), key=lambda kv: -len(kv[1])):
        linhas.append(
            f"| {motivo} | {len(lista)} | {', '.join('#' + str(x.numero) for x in lista[:6])} |"
        )
    return "\n".join(linhas) + "\n"


# ── publicação (PR B) ────────────────────────────────────────────────────────────────────────

CAMPOS_PUBLICAVEIS = (
    "titulo", "orgao_fomento", "url_origem", "resumo_simples", "linguagens", "abrangencia", "uf",
    "inscricao_fim", "fim_tem_horario", "valor_total", "teto_por_projeto", "proponentes",
)  # fmt: skip


def ligada() -> bool:
    """Chave liga/desliga (variável do repositório `PUBLICACAO_AUTOMATICA`). Desligada por padrão."""
    return os.environ.get("PUBLICACAO_AUTOMATICA", "").strip().lower() in ("1", "true", "sim", "on")


def bruto_do_candidato(d: Decisao, hoje: datetime) -> EditalBruto:
    """Decisão que publica -> EditalBruto, validado pelas MESMAS regras da curadoria.

    Honestidade do selo: nada aqui afirma conferência humana. A proveniência é "regex" (lido do texto) e
    `verificado_em` fica vazio; `verificacao = "automatica"` faz o site mostrar o selo.
    """
    dados = {k: d.campos[k] for k in CAMPOS_PUBLICAVEIS if k in d.campos}
    dados.update(
        id_externo=f"auto-{d.numero}",
        verificado_em=hoje.date().isoformat(),
        evidencias=d.evidencias,
    )
    bruto = EditalCurado.model_validate(dados).para_bruto()
    prov = {
        campo: Proveniencia(via="regex", trecho=p.trecho) for campo, p in bruto.proveniencia.items()
    }
    if d.campos.get("uf"):
        prov["uf"] = Proveniencia(via="regex", trecho=f"domínio do site oficial: {host_de(d.link)}")
    return bruto.model_copy(
        update={
            "fonte": FONTE_AUTOMATICA,
            "verificacao": "automatica",
            "verificado_em": None,
            "via": None,
            "proveniencia": prov,
        }
    )


def _rotulos(issue: dict[str, Any]) -> set[str]:
    return {(x["name"] if isinstance(x, dict) else str(x)) for x in issue.get("labels", [])}


async def publicar_automaticos(
    fila: FilaGitHub,
    cliente: ClienteWeb,
    hoje: datetime,
    aprovados: dict[str, DominioAprovado] | None = None,
) -> tuple[list[EditalBruto], list[Decisao]]:
    """Candidatos abertos que passam em TODAS as regras viram editais; a Issue ganha o rótulo `auto-publicado`.

    Roda a cada coleta: o que deixou de valer (prazo mudou, página saiu do ar, veto humano) some do portal no mesmo dia
    e a Issue recebe um comentário explicando.
    """
    issues = await fila.candidatos()
    decisoes = await avaliar_candidatos(
        issues, cliente, aprovados if aprovados is not None else carregar_dominios(), hoje
    )
    rotulos = {int(i["number"]): _rotulos(i) for i in issues}
    brutos: list[EditalBruto] = []
    for d in decisoes:
        if d.publica:
            try:
                brutos.append(bruto_do_candidato(d, hoje))
            except ValueError as exc:  # a curadoria rejeitou: continua na fila humana
                d.publica = False
                d.motivos.append(f"não passou na validação da curadoria: {str(exc)[:120]}")
        tinha = ROTULO_AUTO in rotulos.get(d.numero, set())
        if d.publica and not tinha:
            await fila.rotular(
                d.numero,
                ROTULO_AUTO,
                "Publicado **automaticamente** (sem conferência humana): o prazo e o órgão foram lidos do site oficial, com o trecho "
                "que os comprova. Para tirar do ar, feche esta Issue ou adicione o rótulo `nao-publicar`.",
            )
        elif tinha and not d.publica:
            await fila.desrotular(
                d.numero,
                ROTULO_AUTO,
                f"Saiu do ar automaticamente: {d.motivos[0] if d.motivos else 'não passa mais nas regras'}. "
                "Continua na fila para conferência humana.",
            )
    return brutos, decisoes


async def principal(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Modo sombra da publicação automática (não publica nada)."
    )
    ap.add_argument(
        "--max", type=int, default=0, help="avalia só os N primeiros candidatos (0 = todos)"
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    token, repo = os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("GITHUB_TOKEN/GITHUB_REPOSITORY ausentes: nada a fazer")
        return 0
    hoje = datetime.now(UTC)
    fila = FilaGitHub(repo, token)
    try:
        issues = await fila.candidatos()
    except httpx.HTTPError as exc:
        log.error("não consegui listar os candidatos: %s", exc)
        return 1
    finally:
        await fila.fechar()
    if args.max:
        issues = issues[: args.max]
    async with ClienteWeb() as cliente:
        decisoes = await avaliar_candidatos(issues, cliente, carregar_dominios(), hoje)
    texto = relatorio_md(decisoes, hoje)
    print(texto)
    resumo = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumo:
        Path(resumo).open("a", encoding="utf-8").write(texto)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
