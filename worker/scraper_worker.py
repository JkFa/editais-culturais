"""Entrypoint do ETL: `python -m worker.scraper_worker [--dry-run]`.

Fontes -> padronização -> (LLM opcional) -> snapshot JSON + Supabase.
Falha de uma fonte não derruba as outras; o código de saída só é != 0 se todas falharem.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import ValidationError

from .catalogo import FonteConfig, StatusFonte, carregar_fontes_v4, status_de
from .coletores.curadoria import ErroCuradoria, carregar_curadoria
from .coletores.mapas_culturais import Instancia, Parametros, ResultadoColeta, coletar_instancia
from .dedup import deduplicar
from .enriquecimento_llm import CacheLLM, ClienteGemini, enriquecer_editais
from .fila import FilaGitHub, normalizar_url, publicar_aprovados
from .fontes_v4 import rodar_fontes_v4
from .http import ClienteHttp
from .modelos import Edital, Radar
from .padronizacao import padronizar
from .persistencia import (
    ConfigSupabase,
    Supabase,
    editais_do_snapshot,
    escrever_snapshot,
    ler_snapshot,
    preservar_carimbos,
)
from .web import ClienteWeb

RAIZ = Path(__file__).resolve().parent.parent
SNAPSHOT = RAIZ / "web" / "data" / "snapshot.json"
CACHE_LLM = Path(__file__).resolve().parent / "cache" / "llm_cache.json"
CACHE_VIGIAS = Path(__file__).resolve().parent / "cache" / "vigias.json"
COLETORES_DE_EDITAIS = {
    "minc",
    "cultbr",
}  # fontes que publicam editais (as outras só abrem candidatos)
FONTES = Path(__file__).resolve().parent / "fontes.yaml"
CURADAS = Path(__file__).resolve().parent / "fontes_curadas.yaml"

log = logging.getLogger("worker")


def carregar_env(caminho: Path = RAIZ / ".env") -> None:
    """Lê `.env` (ignorado pelo git) sem sobrescrever variáveis já definidas no ambiente."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        valor = valor.strip().strip("\"'")
        if valor:
            os.environ.setdefault(chave.strip(), valor)


def carregar_fontes(caminho: Path = FONTES) -> tuple[list[Instancia], Parametros]:
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    params = Parametros(**(dados.get("parametros") or {}))
    return [Instancia(**i) for i in dados.get("instancias", [])], params


def padronizar_todos(
    brutos: list[Any], textos: dict[str, str], agora: datetime, nao_mapeadas: set[str]
) -> list[Edital]:
    editais = []
    for bruto in brutos:
        try:
            editais.append(padronizar(bruto, agora, textos.get(bruto.id_externo), nao_mapeadas))
        except (ValidationError, ValueError) as exc:
            log.error(
                "edital %s:%s descartado na padronização: %s",
                bruto.fonte,
                bruto.id_externo,
                str(exc)[:200],
            )
    return editais


def _ler_json(caminho: Path) -> dict[str, Any]:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _gravar_json_se_mudou(caminho: Path, dados: dict[str, Any]) -> None:
    texto = json.dumps(dados, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if not caminho.exists() or caminho.read_text(encoding="utf-8") != texto:
        caminho.write_text(texto, encoding="utf-8")


def _fila_do_ambiente(dry_run: bool) -> FilaGitHub | None:
    """Fila de curadoria (Issues). Só com token e repositório, e nunca em --dry-run."""
    token = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    return FilaGitHub(repo, token) if token and repo and not dry_run else None


def status_das_instancias(
    instancias: list[Instancia],
    ok: set[str],
    falhas: dict[str, str],
    totais: dict[str, int],
    anterior: dict[str, dict[str, Any]],
    agora: datetime,
) -> list[StatusFonte]:
    """`fontes_status` das instâncias de Mapas Culturais (plataformas via API)."""
    saida = []
    for i in instancias:
        cfg = FonteConfig(
            id=i.fonte, nome=i.nome, tipo="plataforma", metodo="api", url=i.base_url,
            termos_url=i.termos_url, termos_conferidos_em=i.termos_conferidos_em,
            ativa=i.ativa, motivo=i.motivo,
        )  # fmt: skip
        if not i.ativa:
            from .catalogo import status_desativada

            saida.append(status_desativada(cfg))
        elif i.fonte in ok:
            saida.append(
                status_de(
                    cfg, "ok", anterior.get(i.fonte), total=totais.get(i.fonte, 0), agora=agora
                )
            )
        else:
            erro = falhas.get(i.fonte, "")
            estado = "bloqueada_em_ci" if "403" in erro else "erro"
            saida.append(
                status_de(cfg, estado, anterior.get(i.fonte), erro=erro or None, agora=agora)
            )  # type: ignore[arg-type]
    return saida


async def executar_fonte_unica(fonte_id: str, dry_run: bool) -> int:
    """`--fonte <id>`: roda fontes isoladas (teste no Actions), sem tocar no snapshot.

    `id` pode ser uma fonte do catálogo, uma instância (`mapas:<slug>`) ou `todas` (todas as
    fontes novas ativas e as instâncias de Mapas, para conferir o que o runner consegue acessar).
    """
    agora = datetime.now(UTC)
    fontes = carregar_fontes_v4(FONTES)
    instancias, params = carregar_fontes()
    ids = {f.id for f in fontes} | {i.fonte for i in instancias}
    if fonte_id != "todas" and fonte_id not in ids:
        print(f"fonte desconhecida: {fonte_id}. Disponíveis: {sorted(ids)} ou 'todas'")
        return 2
    anterior = ler_snapshot(SNAPSHOT) or {}
    status_ant = {x["fonte"]: x for x in anterior.get("fontes", [])}
    urls = {
        normalizar_url(u)
        for e in anterior.get("editais", [])
        for u in (e["url_origem"], e.get("url_inscricao"))
        if u
    }
    estado = _ler_json(CACHE_VIGIAS)
    todas = fonte_id == "todas"
    resultado = await rodar_fontes_v4(
        fontes, agora, urls, set(), estado, status_ant,
        apenas=None if todas else (fonte_id if fonte_id in {f.id for f in fontes} else "__nenhuma__"),
    )  # fmt: skip
    status = list(resultado.status)
    alvo = [i for i in instancias if i.ativa and (todas or i.fonte == fonte_id)]
    if todas:
        alvo = [i for i in alvo if i.slug in ("es", "cim-amfri", "parauapebas")]
    totais: dict[str, int] = {}
    erros: dict[str, str] = {}
    if alvo:
        async with ClienteHttp() as http:
            for inst in alvo:
                try:
                    r = await coletar_instancia(http, inst, params, agora)
                    totais[inst.fonte] = len(r.brutos)
                except Exception as exc:  # noqa: BLE001
                    erros[inst.fonte] = f"{type(exc).__name__}: {exc}"[:300]
        status += status_das_instancias(
            alvo, {i.fonte for i in alvo} - set(erros), erros, totais, status_ant, agora
        )
    for st in status:
        print(json.dumps(st.model_dump(mode="json"), ensure_ascii=False))
    for fid, res in resultado.resultados.items():
        print(
            f"{fid}: editais={len(res.brutos)} candidatos={len(res.candidatos)} "
            f"descartes={dict(res.descartes)}"
        )
    print("RESUMO " + " ".join(f"{st.fonte}={st.status}" for st in status))
    cfg_supabase = None if dry_run else ConfigSupabase.do_ambiente()
    if cfg_supabase:
        sb = Supabase(cfg_supabase)
        try:
            await sb.upsert_fontes_status(status)
        finally:
            await sb.fechar()
    return 0 if all(st.status in ("ok", "sem_dados", "desativada") for st in status) else 1


async def executar(dry_run: bool, snapshot: Path = SNAPSHOT) -> int:
    agora = datetime.now(UTC)
    instancias, params = carregar_fontes()
    ativas = [i for i in instancias if i.ativa]
    for i in instancias:
        if not i.ativa:
            log.info("instância %s desativada: %s", i.fonte, i.motivo or "sem motivo")

    cfg_supabase = None if dry_run else ConfigSupabase.do_ambiente()
    if not dry_run and cfg_supabase is None:
        log.warning("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY ausentes: rodando como --dry-run")
        dry_run = True

    fontes_ok: list[str] = []
    falhas: list[dict[str, str]] = []
    editais: list[Edital] = []
    textos_llm: dict[str, str] = {}
    nao_mapeadas: set[str] = set()
    descartes: dict[str, Counter[str]] = {}
    radar: list[Radar] = []
    ids_por_fonte: dict[str, set[str]] = {}
    totais_por_fonte: dict[str, int] = {}
    erros_por_fonte: dict[str, str] = {}
    anterior_snapshot = ler_snapshot(snapshot) or {}
    status_ant = {x["fonte"]: x for x in anterior_snapshot.get("fontes", [])}
    fila = _fila_do_ambiente(dry_run)

    async with ClienteHttp() as cliente:
        resultados = await asyncio.gather(
            *(coletar_instancia(cliente, i, params, agora) for i in ativas),
            return_exceptions=True,
        )
    for inst, res in zip(ativas, resultados, strict=True):
        if isinstance(res, BaseException):
            log.error("fonte %s falhou: %s", inst.fonte, res)
            falhas.append({"fonte": inst.fonte, "erro": str(res)[:300]})
            erros_por_fonte[inst.fonte] = str(res)[:300]
            continue
        assert isinstance(res, ResultadoColeta)
        convertidos = padronizar_todos(res.brutos, res.textos, agora, nao_mapeadas)
        editais += convertidos
        textos_llm.update(
            {f"{e.fonte}:{e.id_externo}": res.textos[e.id_externo] for e in convertidos}
        )
        ids_por_fonte[inst.fonte] = {e.id_externo for e in convertidos}
        descartes[inst.fonte] = res.descartes
        totais_por_fonte[inst.fonte] = len(convertidos)
        fontes_ok.append(inst.fonte)

    try:
        cur = carregar_curadoria(CURADAS, agora.date())
        for aviso in cur.avisos:
            log.warning(aviso)
        convertidos = padronizar_todos(cur.editais, {}, agora, nao_mapeadas)
        editais += convertidos
        radar = cur.radar
        ids_por_fonte["curadoria"] = {e.id_externo for e in convertidos}
        fontes_ok.append("curadoria")
    except (ErroCuradoria, OSError, yaml.YAMLError) as exc:
        log.error("fonte curadoria falhou: %s", exc)
        falhas.append({"fonte": "curadoria", "erro": str(exc)[:300]})

    # Fila de curadoria: Issues `aprovado` viram editais da fonte "curadoria".
    marcadores: set[str] = set()
    if fila:
        try:
            await fila.garantir_rotulos()
            marcadores = await fila.marcadores_existentes()
            aprovados, avisos_fila = await publicar_aprovados(fila, agora)
            for aviso in avisos_fila:
                log.warning(aviso)
            convertidos = padronizar_todos(aprovados, {}, agora, nao_mapeadas)
            editais += convertidos
            ids_por_fonte.setdefault("curadoria", set()).update(e.id_externo for e in convertidos)
        except httpx.HTTPError as exc:
            log.error("fila de curadoria indisponível: %s", exc)

    # Fontes novas (v4): oficiais, plataformas, descoberta e vigias de página.
    resultado_v4 = None
    catalogo = carregar_fontes_v4(FONTES)
    if True:
        urls_conhecidas = {
            normalizar_url(u)
            for e in editais
            for u in (e.url_origem, e.url_inscricao, e.url_edital_pdf)
            if u
        }
        estado_vigias = _ler_json(CACHE_VIGIAS)
        async with ClienteWeb() as cliente_web:
            resultado_v4 = await rodar_fontes_v4(
                catalogo,
                agora,
                urls_conhecidas,
                marcadores,
                estado_vigias,
                status_ant,
                cliente=cliente_web,
            )
        for fid, res in resultado_v4.resultados.items():
            if res.brutos:
                convertidos = padronizar_todos(res.brutos, res.textos, agora, nao_mapeadas)
                editais += convertidos
                textos_llm.update(
                    {
                        f"{e.fonte}:{e.id_externo}": res.textos.get(e.id_externo, "")
                        for e in convertidos
                    }
                )
                ids_por_fonte[fid] = {e.id_externo for e in convertidos}
                descartes[fid] = res.descartes
            elif (
                next((f.coletor for f in catalogo if f.id == fid), None) in COLETORES_DE_EDITAIS
                and res.status == "ok"
            ):
                ids_por_fonte[fid] = set()
                descartes[fid] = res.descartes
            fontes_ok.append(fid)
        for st in resultado_v4.status:
            if st.status in ("erro", "bloqueada_em_ci", "bloqueada_por_robots"):
                falhas.append({"fonte": st.fonte, "erro": st.erro or st.status})
        if estado_vigias != {**estado_vigias, **resultado_v4.estado_vigias}:
            _gravar_json_se_mudou(CACHE_VIGIAS, {**estado_vigias, **resultado_v4.estado_vigias})

    if not fontes_ok:
        log.error("todas as fontes falharam; snapshot anterior preservado")
        return 1

    anterior = ler_snapshot(snapshot)
    # Fonte que falhou hoje: mantém o que ela tinha no snapshot anterior (ainda aberto).
    falharam = {f["fonte"] for f in falhas}
    editais += [e for e in editais_do_snapshot(anterior) if e.fonte in falharam]
    if "curadoria" in falharam and anterior:
        radar = [Radar.model_validate(r) for r in anterior.get("radar", [])]

    # Deduplicação entre fontes: um registro canônico, demais origens em `fontes_alternativas`.
    tipos_por_fonte = {f.id: f.tipo for f in catalogo}
    tipos_por_fonte.update({i.fonte: "plataforma" for i in instancias})
    editais, duplicados = deduplicar(editais, tipos_por_fonte)
    if duplicados:
        log.info("deduplicação: %d registros viraram fontes alternativas", duplicados)
        vivos = {(e.fonte, e.id_externo) for e in editais}
        for fonte in list(ids_por_fonte):
            ids_por_fonte[fonte] = {i for i in ids_por_fonte[fonte] if (fonte, i) in vivos}

    # LLM opcional: só para editais de coleta (a curadoria já é conferida) e fontes ok.
    chave_gemini = os.environ.get("GEMINI_API_KEY", "").strip()
    cache = CacheLLM(CACHE_LLM)
    if chave_gemini:
        gemini = ClienteGemini(chave_gemini, os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash")
        try:
            alvo = [e for e in editais if e.fonte.startswith("mapas:") and e.fonte not in falharam]
            outros = [e for e in editais if e not in alvo]
            maximo = int(os.environ.get("GEMINI_MAX_POR_EXECUCAO", "60"))
            editais = outros + await enriquecer_editais(alvo, textos_llm, gemini, cache, maximo)
        finally:
            await gemini.fechar()
        cache.salvar()
    else:
        log.info("GEMINI_API_KEY ausente: enriquecimento LLM desligado (só regex)")

    supabase = Supabase(cfg_supabase) if cfg_supabase else None
    existentes: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        if supabase:
            try:
                existentes = await supabase.existentes()
            except RuntimeError as exc:
                log.error("não foi possível ler o banco: %s", exc)
        # Carimbos (coletado_em) vêm do banco; sem banco, do snapshot anterior. A decisão de
        # gravar compara só com o banco: o snapshot não prova que a linha está no Supabase.
        carimbos = existentes or {
            (x["fonte"], x["id_externo"]): x for x in (anterior or {}).get("editais", [])
        }
        editais = preservar_carimbos(editais, carimbos)

        # Candidatos viram Issues (fila de curadoria). Em --dry-run só são contados.
        criados: dict[str, int] = {}
        if resultado_v4:
            todos = resultado_v4.candidatos
            if fila:
                try:
                    for fid, cand in todos:
                        if cand.id in marcadores:
                            continue
                        await fila.criar(cand)
                        marcadores.add(cand.id)
                        criados[fid] = criados.get(fid, 0) + 1
                except httpx.HTTPError as exc:
                    log.error("não consegui criar candidato na fila: %s", exc)
            log.info(
                "candidatos: %d propostos, %d Issues criadas", len(todos), sum(criados.values())
            )
        status_fontes = status_das_instancias(
            instancias, {f for f in fontes_ok if f.startswith("mapas:")},
            erros_por_fonte, totais_por_fonte, status_ant, agora,
        ) + (resultado_v4.status if resultado_v4 else [])  # fmt: skip
        status_fontes += [
            status_de(
                FonteConfig(id="curadoria", nome="Curadoria manual (fontes_curadas.yaml e fila)", tipo="oficial",
                            metodo="html", url="https://github.com/JkFa/editais-culturais/issues?q=label%3Aaprovado"),
                "ok" if "curadoria" in fontes_ok else "erro", status_ant.get("curadoria"),
                total=sum(1 for e in editais if e.fonte == "curadoria"), agora=agora,
            )
        ]  # fmt: skip

        corpo = escrever_snapshot(
            snapshot, editais, radar, fontes_ok, falhas, agora,
            [s.model_dump(mode="json") for s in status_fontes],
        )  # fmt: skip
        imprimir_resumo(corpo, descartes, nao_mapeadas, dry_run)

        if supabase:
            try:
                novos = [
                    e
                    for e in editais
                    if existentes.get((e.fonte, e.id_externo), {}).get("hash_conteudo")
                    != e.hash_conteudo
                ]
                gravados = await supabase.upsert_editais(novos)
                removidos = 0
                for fonte in fontes_ok:
                    if fonte not in ids_por_fonte:  # vigia/descoberta: não geram editais
                        continue
                    removidos += await supabase.remover_sumidos(fonte, ids_por_fonte[fonte], agora)
                if "curadoria" not in falharam:
                    await supabase.sincronizar_radar(radar, agora)
                await supabase.upsert_fontes_status(status_fontes)
                await supabase.heartbeat(agora)
                await supabase.limpar_antigos()
                log.info(
                    "Supabase: %d gravados, %d removidos, %d inalterados",
                    gravados,
                    removidos,
                    len(editais) - gravados,
                )
            except RuntimeError as exc:
                log.error("falha ao gravar no Supabase (snapshot já foi escrito): %s", exc)
                return 3
    finally:
        if supabase:
            await supabase.fechar()
        if fila:
            await fila.fechar()
    return 0


def imprimir_resumo(
    corpo: dict[str, Any], descartes: dict[str, Counter[str]], nao_mapeadas: set[str], dry_run: bool
) -> None:
    editais = corpo["editais"]
    por_fonte = Counter(e["fonte"] for e in editais)
    print(f"\n== Resumo da coleta ({'dry-run' if dry_run else 'completa'}) ==")
    print(f"gerado_em: {corpo['gerado_em']}")
    print(f"editais abertos no snapshot: {len(editais)}")
    for fonte, n in sorted(por_fonte.items()):
        extra = f"  descartes={dict(descartes[fonte])}" if descartes.get(fonte) else ""
        print(f"  {fonte:22} {n:4}{extra}")
    print(f"fontes ok: {len(corpo['fontes_ok'])}  falharam: {len(corpo['fontes_falharam'])}")
    for f in corpo["fontes_falharam"]:
        print(f"  FALHOU {f['fonte']}: {f['erro']}")
    com_valor = sum(
        1 for e in editais if e["valor_total"] is not None or e["teto_por_projeto"] is not None
    )
    print(f"com valor em R$ informado: {com_valor}; radar: {len(corpo['radar'])}")
    if nao_mapeadas:
        print(f"áreas não mapeadas (foram para Multiáreas): {sorted(nao_mapeadas)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coleta editais culturais.")
    parser.add_argument(
        "--dry-run", action="store_true", help="coleta e gera o snapshot sem tocar no Supabase"
    )
    parser.add_argument(
        "--fonte", help="roda só uma fonte nova (id de worker/fontes.yaml), sem alterar o snapshot"
    )
    args = parser.parse_args(argv)
    carregar_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.fonte:
        return asyncio.run(executar_fonte_unica(args.fonte, args.dry_run))
    return asyncio.run(executar(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
