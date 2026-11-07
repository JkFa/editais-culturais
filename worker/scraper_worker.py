"""Entrypoint do ETL: `python -m worker.scraper_worker [--dry-run]`.

Fontes -> padronização -> (LLM opcional) -> snapshot JSON + Supabase.
Falha de uma fonte não derruba as outras; o código de saída só é != 0 se todas falharem.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .coletores.curadoria import ErroCuradoria, carregar_curadoria
from .coletores.mapas_culturais import Instancia, Parametros, ResultadoColeta, coletar_instancia
from .enriquecimento_llm import CacheLLM, ClienteGemini, enriquecer_editais
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

RAIZ = Path(__file__).resolve().parent.parent
SNAPSHOT = RAIZ / "web" / "data" / "snapshot.json"
CACHE_LLM = Path(__file__).resolve().parent / "cache" / "llm_cache.json"
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

    async with ClienteHttp() as cliente:
        resultados = await asyncio.gather(
            *(coletar_instancia(cliente, i, params, agora) for i in ativas),
            return_exceptions=True,
        )
    for inst, res in zip(ativas, resultados, strict=True):
        if isinstance(res, BaseException):
            log.error("fonte %s falhou: %s", inst.fonte, res)
            falhas.append({"fonte": inst.fonte, "erro": str(res)[:300]})
            continue
        assert isinstance(res, ResultadoColeta)
        convertidos = padronizar_todos(res.brutos, res.textos, agora, nao_mapeadas)
        editais += convertidos
        textos_llm.update(
            {f"{e.fonte}:{e.id_externo}": res.textos[e.id_externo] for e in convertidos}
        )
        ids_por_fonte[inst.fonte] = {e.id_externo for e in convertidos}
        descartes[inst.fonte] = res.descartes
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

    if not fontes_ok:
        log.error("todas as fontes falharam; snapshot anterior preservado")
        return 1

    anterior = ler_snapshot(snapshot)
    # Fonte que falhou hoje: mantém o que ela tinha no snapshot anterior (ainda aberto).
    falharam = {f["fonte"] for f in falhas}
    editais += [e for e in editais_do_snapshot(anterior) if e.fonte in falharam]
    if "curadoria" in falharam and anterior:
        radar = [Radar.model_validate(r) for r in anterior.get("radar", [])]

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
        if not existentes and anterior:
            existentes = {(x["fonte"], x["id_externo"]): x for x in anterior.get("editais", [])}
        editais = preservar_carimbos(editais, existentes)

        corpo = escrever_snapshot(snapshot, editais, radar, fontes_ok, falhas, agora)
        imprimir_resumo(corpo, descartes, nao_mapeadas, dry_run)

        if supabase:
            try:
                novos = [
                    e
                    for e in editais
                    if e.fonte not in falharam
                    and existentes.get((e.fonte, e.id_externo), {}).get("hash_conteudo")
                    != e.hash_conteudo
                ]
                gravados = await supabase.upsert_editais(novos)
                removidos = 0
                for fonte in fontes_ok:
                    removidos += await supabase.remover_sumidos(fonte, ids_por_fonte[fonte], agora)
                if "curadoria" not in falharam:
                    await supabase.sincronizar_radar(radar, agora)
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
    args = parser.parse_args(argv)
    carregar_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(executar(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
