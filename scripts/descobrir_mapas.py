"""Descobre instâncias do Mapas Culturais ainda não monitoradas e propõe cadastrá-las (sempre `ativa: false`).

Funil (do mais barato ao mais caro):
  1. gera endereços candidatos (UFs, capitais, municípios com mais de 50 mil habitantes e hosts "mapa*" vistos nos
     links do snapshot) a partir de padrões conhecidos;
  2. descarta quem já está no `fontes.yaml` ou foi sondado há pouco (cache `worker/referencia/mapas_sondados.json`);
  3. resolve o DNS, com controle de wildcard por domínio (um domínio que responde a qualquer nome não prova nada);
  4. só para quem resolve: `robots.txt` e UMA requisição a `/api/opportunity/find?@limit=1`, com User-Agent honesto,
     intervalo entre pedidos e nenhuma tentativa de contornar bloqueio (403/429/robots viram "bloqueado").

Quem responde como Mapas Culturais entra no `fontes.yaml` com `ativa: false` e a sondagem registrada. A pessoa
revisa robots.txt e termos de uso e só então ativa. Nada é coletado aqui: é uma sondagem de existência.

Uso: python -m scripts.descobrir_mapas [--dry-run] [--max-sondas 60] [--so-dns] [--corpo-pr arquivo.md]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import secrets
import socket
import sys
import unicodedata
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from worker.web import BloqueadoPorRobots, BloqueioDeAcesso, ClienteWeb, ErroWeb

log = logging.getLogger("descobrir_mapas")
RAIZ = Path(__file__).resolve().parent.parent
FONTES = RAIZ / "worker" / "fontes.yaml"
REFERENCIA = RAIZ / "worker" / "referencia"
SONDADOS = REFERENCIA / "mapas_sondados.json"
SNAPSHOT = RAIZ / "web" / "data" / "snapshot.json"

MAX_SONDAS_PADRAO = 60
INTERVALO_S = 2.0
TIMEOUT_SONDA_S = 25.0
# Quanto tempo um resultado negativo vale antes de testar de novo.
REVALIDAR_DIAS = {"nao_mapas": 90, "inalcancavel": 14, "bloqueado": 30}

PADROES_UF = [
    "mapacultural.{u}.gov.br", "mapa.cultura.{u}.gov.br", "mapacultural.secult.{u}.gov.br",
    "mapacultural.cultura.{u}.gov.br", "mapas.cultura.{u}.gov.br", "mapa.cultural.{u}.gov.br",
    "mapasculturais.{u}.gov.br",
]  # fmt: skip
PADROES_MUNICIPIO = [
    "mapacultural.{m}.{u}.gov.br", "mapa.cultura.{m}.{u}.gov.br", "mapas.{m}.{u}.gov.br",
    "mapacultural.{m}.com.br",
]  # fmt: skip
PRIMEIRO_ROTULO_MAPAS = re.compile(
    r"^(mapa|mapas|mapacultural|mapascuturais|mapasculturais|mapa-cultural)$"
)


def slug(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "", t)


@dataclass
class Candidato:
    host: str
    ente: str  # estado | capital | municipio | desconhecido
    uf: str | None
    municipio: str | None = None
    origem: str = "padrao"
    prioridade: int = 5  # menor = antes

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"


def _ler_json(caminho: Path, padrao: Any) -> Any:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def gerar_candidatos(
    ufs: list[dict[str, Any]], municipios: list[dict[str, Any]], hosts_vistos: set[str]
) -> list[Candidato]:
    """Endereços prováveis, sem repetir host. Estados e capitais primeiro, depois por população."""
    saida: dict[str, Candidato] = {}

    def add(c: Candidato) -> None:
        saida.setdefault(c.host, c)

    for u in ufs:
        for p in PADROES_UF:
            add(Candidato(p.format(u=u["uf"].lower()), "estado", u["uf"], None, "padrao-uf", 1))
    capitais = {(u["capital_ibge"]) for u in ufs}
    for ordem, m in enumerate(sorted(municipios, key=lambda x: -x["populacao"])):
        capital = m["ibge"] in capitais
        for p in PADROES_MUNICIPIO:
            host = p.format(m=slug(m["nome"]), u=m["uf"].lower())
            add(Candidato(host, "capital" if capital else "municipio", m["uf"], m["nome"], "padrao-municipio", 2 if capital else 3 + min(ordem // 100, 4)))  # fmt: skip
    for h in sorted(hosts_vistos):
        sufixo = re.search(r"\.([a-z]{2})\.gov\.br$", h)
        add(
            Candidato(
                h,
                "desconhecido",
                sufixo.group(1).upper() if sufixo else None,
                None,
                "link-visto",
                0,
            )
        )
    return sorted(saida.values(), key=lambda c: (c.prioridade, c.host))


def hosts_de_mapas_nos_links(snapshot: dict[str, Any]) -> set[str]:
    """Hosts que parecem Mapas Culturais citados em qualquer link do snapshot (editais e radar)."""
    urls: list[str] = []
    for e in snapshot.get("editais", []):
        urls += [e.get("url_origem"), e.get("url_inscricao"), e.get("url_edital_pdf")]
        urls += [
            a.get("url") if isinstance(a, dict) else a for a in e.get("fontes_alternativas") or []
        ]
    for r in snapshot.get("radar", []):
        urls += [r.get("url"), r.get("url_origem")]
    achados = set()
    for u in urls:
        if not u:
            continue
        host = urlparse(str(u)).netloc.lower().removeprefix("www.")
        if host and PRIMEIRO_ROTULO_MAPAS.match(host.split(".")[0]):
            achados.add(host)
    return achados


def hosts_conhecidos(texto_yaml: str) -> set[str]:
    """Hosts já cadastrados em `instancias` (ativos ou não)."""
    dados = yaml.safe_load(texto_yaml) or {}
    return {
        urlparse(i["base_url"]).netloc.lower()
        for i in dados.get("instancias", [])
        if i.get("base_url")
    }


def a_sondar(
    candidatos: list[Candidato], conhecidos: set[str], sondados: dict[str, Any], hoje: date
) -> list[Candidato]:
    saida = []
    for c in candidatos:
        if c.host in conhecidos:
            continue
        antes = sondados.get(c.host)
        if antes:
            validade = REVALIDAR_DIAS.get(antes["resultado"])
            if (
                validade is None
            ):  # "proposto": já virou bloco no fontes.yaml (para retestar, apague a entrada do cache)
                continue
            if date.fromisoformat(antes["data"]) + timedelta(days=validade) > hoje:
                continue
        saida.append(c)
    return saida


# ── DNS ──────────────────────────────────────────────────────────────────────────────────────

Resolvedor = Callable[[str], Awaitable[bool]]


_POOL_DNS = ThreadPoolExecutor(max_workers=128, thread_name_prefix="dns")


async def resolver_dns(host: str) -> bool:
    """Resolve o nome em threads próprias (o executor padrão do asyncio é pequeno demais para milhares de nomes).

    Só `socket.gaierror` (NXDOMAIN e afins) é resposta definitiva "não existe". Timeout sob carga tenta de novo
    uma vez com mais paciência: confundir lentidão com inexistência esconderia instâncias reais.
    """
    loop = asyncio.get_running_loop()
    for espera in (6.0, 15.0):
        try:
            await asyncio.wait_for(
                loop.run_in_executor(_POOL_DNS, socket.gethostbyname, host), espera
            )
            return True
        except socket.gaierror:
            return False
        except TimeoutError:
            continue
    return False


async def filtrar_por_dns(
    candidatos: list[Candidato], resolvedor: Resolvedor = resolver_dns, paralelo: int = 128
) -> tuple[list[Candidato], list[Candidato], set[str]]:
    """(resolvem de verdade, não resolvem, zonas com wildcard). Zona com wildcard não prova nada."""
    sem = asyncio.Semaphore(paralelo)

    async def tenta(host: str) -> bool:
        async with sem:
            return await resolvedor(host)

    resultados = await asyncio.gather(*(tenta(c.host) for c in candidatos))
    # O controle de wildcard só interessa onde algo resolveu (poupa milhares de consultas inúteis).
    zonas = sorted(
        {c.host.split(".", 1)[1] for c, ok in zip(candidatos, resultados, strict=True) if ok}
    )
    controle = await asyncio.gather(*(tenta(f"x{secrets.token_hex(5)}.{z}") for z in zonas))
    wildcard = {z for z, ok in zip(zonas, controle, strict=True) if ok}
    reais, nao = [], []
    for c, ok in zip(candidatos, resultados, strict=True):
        if ok and c.host.split(".", 1)[1] not in wildcard:
            reais.append(c)
        elif not ok:
            nao.append(c)
    return reais, nao, wildcard


# ── sondagem ─────────────────────────────────────────────────────────────────────────────────


@dataclass
class Sondagem:
    candidato: Candidato
    resultado: str  # mapas | nao_mapas | bloqueado | inalcancavel
    detalhe: str = ""
    http: int | None = None
    robots: str = "não consultado"
    confianca: str | None = None  # alta (lista com itens do formato Mapas) | baixa (lista vazia)
    amostra: dict[str, Any] = field(default_factory=dict)


async def sondar(cliente: ClienteWeb, c: Candidato, timeout_s: float = TIMEOUT_SONDA_S) -> Sondagem:
    api = f"{c.base_url}/api/opportunity/find"
    consulta = f"{api}?@limit=1&@select=id,name"
    s = Sondagem(c, "nao_mapas")

    async def passo() -> Sondagem:
        pode, _ = await cliente.permitido(api)
        s.robots = "permitido" if pode else "NÃO permite /api/"
        if not pode:
            s.resultado, s.detalhe = "bloqueado", "robots.txt não permite a API"
            return s
        r = await cliente.obter(consulta, intervalo_s=INTERVALO_S)
        s.http = r.status
        if "json" not in r.tipo.lower():
            s.detalhe = f"resposta não é JSON ({r.tipo or 'sem tipo'})"
            return s
        try:
            dados = json.loads(r.conteudo)
        except ValueError:
            s.detalhe = "JSON inválido"
            return s
        if not isinstance(dados, list):
            s.detalhe = "JSON não é lista"
            return s
        if dados and isinstance(dados[0], dict) and "id" in dados[0] and "name" in dados[0]:
            s.resultado, s.confianca = "mapas", "alta"
            s.amostra = {"id": dados[0]["id"], "name": str(dados[0]["name"])[:80]}
        elif not dados:
            s.resultado, s.confianca = "mapas", "baixa"
            s.detalhe = "lista vazia (compatível com Mapas sem oportunidades; confirmar na página)"
        else:
            s.detalhe = "lista com outro formato"
        return s

    try:
        return await asyncio.wait_for(passo(), timeout_s)
    except (BloqueioDeAcesso, BloqueadoPorRobots) as exc:
        s.resultado, s.detalhe = "bloqueado", str(exc)
    except ErroWeb as exc:
        texto = str(exc)
        s.resultado = "nao_mapas" if re.search(r"HTTP (404|410|405|401)", texto) else "inalcancavel"
        s.detalhe = texto[:160]
    except (TimeoutError, httpx.HTTPError) as exc:
        s.resultado, s.detalhe = "inalcancavel", f"{type(exc).__name__}"
    return s


# ── saída: fontes.yaml, cache e corpo do PR ──────────────────────────────────────────────────


def bloco_yaml(s: Sondagem, hoje: date, slugs_usados: set[str]) -> tuple[str, str]:
    c = s.candidato
    if c.ente == "estado":
        base, nome, abr = c.uf.lower(), f"Mapa Cultural: {c.uf} (estado)", "estadual"
    elif c.municipio:
        base, nome, abr = (
            f"{slug(c.municipio)}-{c.uf.lower()}",
            f"Mapa Cultural de {c.municipio} ({c.uf})",
            "municipal",
        )
    else:
        base, nome, abr = (
            slug(c.host.replace(".gov.br", "").replace(".com.br", "")) or "novo",
            f"Mapa Cultural ({c.host})",
            None,
        )
    sl, n = base, 2
    while sl in slugs_usados:
        sl, n = f"{base}-{n}", n + 1
    slugs_usados.add(sl)
    sondagem = {
        "data": hoje.isoformat(),
        "http": s.http,
        "robots": s.robots,
        "confianca": s.confianca,
        "origem": c.origem,
    }
    linhas = [
        f"  - slug: {sl}",
        f"    nome: {json.dumps(nome, ensure_ascii=False)}",
        f"    base_url: {c.base_url}",
        f"    abrangencia: {abr if abr else 'null'}",
        f"    uf: {c.uf if c.uf else 'null'}",
        f"    municipio: {json.dumps(c.municipio, ensure_ascii=False) if c.municipio else 'null'}",
        "    ativa: false",
        f'    motivo: "Descoberta automática em {hoje.isoformat()}: aguarda conferência do robots.txt e dos termos de uso por uma pessoa."',
        f'    descoberta: "sondagem de {hoje.isoformat()}: /api/opportunity/find respondeu como Mapas Culturais (confiança {s.confianca})"',
        f"    sondagem: {json.dumps(sondagem, ensure_ascii=False)}",
    ]
    return sl, "\n".join(linhas) + "\n"


def inserir_instancias(texto: str, blocos: list[str]) -> str:
    """Acrescenta os blocos ao fim da lista `instancias`, antes do cabeçalho comentado das fontes v4."""
    m = re.search(r"\n(# -{20,}\n# v4: novas fontes)", texto)
    if not m:
        raise ValueError("não achei onde termina a lista de instâncias em fontes.yaml")
    return texto[: m.start()] + "\n" + "\n".join(blocos) + texto[m.start() :]


def corpo_do_pr(achados: list[Sondagem], hoje: date, resumo: dict[str, int]) -> str:
    linhas = [
        f"Sondagem automática de {hoje.isoformat()}. Cada instância abaixo respondeu a `/api/opportunity/find` como Mapas Culturais "
        "e foi cadastrada com `ativa: false`. **Nada foi coletado.**",
        "",
        "| Território | Endereço | Confiança | robots.txt | Amostra |",
        "|---|---|---|---|---|",
    ]
    for s in achados:
        c = s.candidato
        terr = f"{c.municipio or 'Estado'} ({c.uf})" if c.uf else "?"
        linhas.append(
            f"| {terr} | {c.base_url} | {s.confianca} | {s.robots} | {s.amostra.get('name') or s.detalhe or '—'} |"
        )
    linhas += [
        "",
        "## Para ativar uma instância",
        "1. Abra o `robots.txt` e os termos de uso do site e confirme que a consulta à API pública é permitida.",
        "2. Em `worker/fontes.yaml`: troque `ativa: true`, apague `motivo`, preencha `termos_url` e `termos_conferidos_em`.",
        "3. Instâncias de baixa confiança (lista vazia): abra a página e confirme que é Mapas Culturais.",
        '4. As que você não for ativar: remova o bloco. O endereço fica no cache como "proposto" e não será proposto de novo.',
        "",
        "Resumo da sondagem: " + ", ".join(f"{k}: {v}" for k, v in sorted(resumo.items())),
    ]
    return "\n".join(linhas) + "\n"


@dataclass
class Resultado:
    candidatos: int = 0
    a_sondar: int = 0
    resolvem: int = 0
    wildcard: set[str] = field(default_factory=set)
    sondagens: list[Sondagem] = field(default_factory=list)
    achados: list[Sondagem] = field(default_factory=list)

    def resumo(self) -> dict[str, int]:
        cont: dict[str, int] = {}
        for s in self.sondagens:
            cont[s.resultado] = cont.get(s.resultado, 0) + 1
        return cont


async def descobrir(
    texto_fontes: str,
    sondados: dict[str, Any],
    refs: dict[str, Any],
    snapshot: dict[str, Any],
    cliente: ClienteWeb,
    hoje: date,
    max_sondas: int = MAX_SONDAS_PADRAO,
    resolvedor: Resolvedor = resolver_dns,
    so_dns: bool = False,
) -> Resultado:
    """Roda o funil e devolve o que achou; `sondados` (cache) é atualizado no lugar."""
    res = Resultado()
    cands = gerar_candidatos(refs["ufs"], refs["municipios"], hosts_de_mapas_nos_links(snapshot))
    res.candidatos = len(cands)
    pendentes = a_sondar(cands, hosts_conhecidos(texto_fontes), sondados, hoje)
    res.a_sondar = len(pendentes)
    reais, nao, res.wildcard = await filtrar_por_dns(pendentes, resolvedor)
    res.resolvem = len(reais)
    # Quem não resolve não vai para o cache (milhares de linhas): refazer o DNS toda semana é barato e não toca nos sites.
    del nao
    if so_dns:
        return res
    # Hosts diferentes em paralelo (o ClienteWeb já espera o intervalo POR domínio, então cada site recebe um pedido por vez).
    sem = asyncio.Semaphore(8)

    async def uma(c: Candidato) -> Sondagem:
        async with sem:
            return await sondar(cliente, c)

    for s in await asyncio.gather(*(uma(c) for c in reais[:max_sondas])):
        c = s.candidato
        res.sondagens.append(s)
        log.info("%s: %s %s", c.host, s.resultado, s.detalhe)
        sondados[c.host] = {"data": hoje.isoformat(), "resultado": "proposto" if s.resultado == "mapas" else s.resultado, "detalhe": s.detalhe, "http": s.http}  # fmt: skip
        if s.resultado == "mapas":
            res.achados.append(s)
    return res


async def principal(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dry-run", action="store_true", help="não altera fontes.yaml nem o cache")
    ap.add_argument("--max-sondas", type=int, default=MAX_SONDAS_PADRAO)
    ap.add_argument(
        "--so-dns",
        action="store_true",
        help="para depois do filtro de DNS (nenhuma requisição ao site)",
    )
    ap.add_argument(
        "--corpo-pr", type=Path, help="onde gravar o texto do PR (só se houver achados)"
    )
    ap.add_argument("--fontes", type=Path, default=FONTES)
    ap.add_argument("--sondados", type=Path, default=SONDADOS)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    hoje = datetime.now(UTC).date()
    texto = args.fontes.read_text(encoding="utf-8")
    sondados: dict[str, Any] = _ler_json(args.sondados, {}).get("hosts", {})
    refs = {
        "ufs": _ler_json(REFERENCIA / "ufs_capitais.json", {}).get("ufs", []),
        "municipios": _ler_json(REFERENCIA / "municipios_50k.json", {}).get("municipios", []),
    }
    snapshot = _ler_json(SNAPSHOT, {})
    async with ClienteWeb() as cliente:
        res = await descobrir(
            texto, sondados, refs, snapshot, cliente, hoje, args.max_sondas, so_dns=args.so_dns
        )
    print(
        f"candidatos: {res.candidatos}; a sondar: {res.a_sondar}; resolvem no DNS: {res.resolvem}; domínios com wildcard: {sorted(res.wildcard) or '-'}"
    )
    print(
        f"sondados: {len(res.sondagens)} {res.resumo()}; instâncias de Mapas encontradas: {len(res.achados)}"
    )
    for s in res.achados:
        print(
            f"  + {s.candidato.base_url} ({s.candidato.municipio or s.candidato.uf}) confiança {s.confianca}"
        )
    if args.dry_run:
        return 0
    if res.achados:
        usados = {i["slug"] for i in (yaml.safe_load(texto) or {}).get("instancias", [])}
        blocos = [bloco_yaml(s, hoje, usados)[1] for s in res.achados]
        novo = inserir_instancias(texto, blocos)
        yaml.safe_load(novo)  # garante que continua sendo YAML válido
        args.fontes.write_text(novo, encoding="utf-8")
        if args.corpo_pr:
            args.corpo_pr.write_text(corpo_do_pr(res.achados, hoje, res.resumo()), encoding="utf-8")
    args.sondados.write_text(
        json.dumps(
            {"atualizado_em": hoje.isoformat(), "hosts": dict(sorted(sondados.items()))},
            ensure_ascii=False,
            indent=0,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
