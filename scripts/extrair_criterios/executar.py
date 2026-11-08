"""Orquestra a extração diária. Falha de um edital nunca derruba os outros; falha de tudo nunca derruba a coleta
principal (este passo roda em job próprio, depois dela)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from worker.persistencia import ConfigSupabase
from worker.web import BloqueadoPorRobots, BloqueioDeAcesso, ClienteWeb, ErroWeb

from .banco import Banco
from .documento import SemDocumento, baixar_documento
from .extracao import ErroExtracao, extrair
from .fila import Plano, planejar, url_do_documento
from .llm import ErroLlm, Provedor, ProvedorGemini

log = logging.getLogger("extrair_criterios")
MAX_EDITAIS_PADRAO = 20


@dataclass
class Resumo:
    processados: int = 0
    ok: int = 0
    inalterados: int = 0
    erro: int = 0
    sem_documento: int = 0
    restantes: int = 0
    interrompido: str | None = None
    detalhes: list[str] = field(default_factory=list)

    def linhas(self) -> list[str]:
        saida = [
            "Extração de critérios: resumo",
            f"  processados:   {self.processados}",
            f"  ok:            {self.ok} (sem mudança de documento: {self.inalterados})",
            f"  erro:          {self.erro}",
            f"  sem documento: {self.sem_documento}",
            f"  na fila ainda: {self.restantes}",
        ]
        if self.interrompido:
            saida.append(f"  interrompido:  {self.interrompido}")
        return saida


def max_editais_do_ambiente() -> int:
    try:
        return max(0, int(os.environ.get("MAX_EDITAIS_POR_DIA", MAX_EDITAIS_PADRAO)))
    except ValueError:
        return MAX_EDITAIS_PADRAO


async def processar_um(
    e: dict[str, Any], banco: Banco | None, web: ClienteWeb, provedor: Provedor, anterior: dict[str, Any] | None,
    tentativas: int, agora: datetime, dry_run: bool, resumo: Resumo, ignorar_hash: bool = False,
) -> None:  # fmt: skip
    eid = e["id"]
    rotulo = f"edital {eid} ({(e.get('titulo') or '')[:50]})"

    async def marcar(status: str, erro: str | None = None, tent: int | None = None) -> None:
        if banco and not dry_run:
            await banco.marcar(eid, status, erro, tent)

    resumo.processados += 1
    try:
        doc = await baixar_documento(web, url_do_documento(e))
    except SemDocumento as exc:
        resumo.sem_documento += 1
        log.info("%s: sem documento: %s", rotulo, exc)
        await marcar("sem_documento", str(exc), 0)
        return
    except (
        BloqueioDeAcesso,
        BloqueadoPorRobots,
    ) as exc:  # não contornamos; tentar de novo não adianta
        resumo.sem_documento += 1
        log.info("%s: acesso negado: %s", rotulo, exc)
        await marcar("sem_documento", f"acesso negado: {exc}", 0)
        return
    except ErroWeb as exc:
        resumo.erro += 1
        log.warning("%s: erro ao baixar: %s", rotulo, exc)
        await marcar("erro", f"download: {exc}", tentativas + 1)
        return

    if (
        not ignorar_hash
        and anterior
        and anterior.get("fonte_hash") == doc.sha256
        and anterior.get("fonte_url") == doc.url
    ):
        resumo.ok += 1
        resumo.inalterados += 1  # o documento não mudou: nada de LLM, só o carimbo
        log.info("%s: documento sem mudança (hash igual); critérios mantidos", rotulo)
        await marcar("ok", None, 0)
        return

    await marcar("processando", None, tentativas)
    try:
        res = await extrair(doc, provedor, e.get("titulo"))
    except ErroExtracao as exc:
        resumo.erro += 1
        log.warning("%s: extração inválida: %s", rotulo, exc)
        await marcar("erro", str(exc), tentativas + 1)
        return
    linha = {
        "edital_id": eid, "fonte_url": doc.url, "fonte_hash": doc.sha256, **res.campos,
        "extraido_em": agora.isoformat(), "modelo_llm": provedor.modelo,
        "revisado": False, "revisado_em": None, "revisado_por": None,
    }  # fmt: skip
    if res.vazio:
        linha["observacoes"] = (
            linha["observacoes"]
            or "Nada aproveitável foi encontrado no documento (os critérios podem estar em anexo separado)."
        )
    if anterior and anterior.get("revisado"):
        log.warning("%s: o documento mudou e os critérios já revisados voltam para revisão", rotulo)
    if banco and not dry_run:
        await banco.salvar_criterios(linha)
    elif dry_run:
        log.info("%s: (dry-run) %s", rotulo, {k: v for k, v in res.campos.items() if v})
    await marcar("ok", None, 0)
    resumo.ok += 1
    log.info("%s: ok (%d parte(s), %d descarte(s))", rotulo, res.partes, len(res.descartes))


async def executar(
    banco: Banco | None,
    web: ClienteWeb,
    provedor: Provedor | None,
    *,
    max_editais: int,
    agora: datetime | None = None,
    dry_run: bool = False,
    editais: list[dict[str, Any]] | None = None,
    forcar: bool = False,
) -> Resumo:
    agora = agora or datetime.now(UTC)
    resumo = Resumo()
    if banco is None:
        raise RuntimeError("sem Supabase configurado")
    abertos = (
        editais
        if editais is not None
        else [x for x in await banco.editais_abertos() if x.get("ambito", "brasil") == "brasil"]
    )
    fila, criterios, projetos = (
        await banco.fila(),
        await banco.criterios_existentes(),
        await banco.projetos_por_edital(),
    )
    if forcar and editais is not None:  # `--edital N`: reprocessa mesmo que já esteja ok
        plano = Plano(processar=list(editais), elegiveis=len(editais))
    else:
        plano = planejar(abertos, fila, criterios, projetos, agora, max_editais)
    if not dry_run:
        await banco.enfileirar(plano.enfileirar)
    if provedor is None:
        resumo.restantes = plano.elegiveis
        resumo.interrompido = "GEMINI_API_KEY ausente: só a fila foi atualizada"
        return resumo
    for e in plano.processar:
        tent = int((fila.get(e["id"]) or {}).get("tentativas") or 0)
        try:
            # "Reprocessar" (tela de revisão) deixa o motivo na fila: a extração roda mesmo com o hash igual
            pedido = forcar or (fila.get(e["id"]) or {}).get("ultimo_erro") == "reprocessar"
            await processar_um(
                e,
                banco,
                web,
                provedor,
                criterios.get(e["id"]),
                tent,
                agora,
                dry_run,
                resumo,
                pedido,
            )
        except ErroLlm as exc:
            if exc.tipo in ("config", "cota"):  # sem chave ou sem cota: parar de gastar tentativas
                if not dry_run:
                    await banco.marcar(e["id"], "pendente", None, tent)
                resumo.processados -= 1
                resumo.interrompido = f"{exc.tipo}: {exc}"
                break
            resumo.erro += 1
            log.warning("edital %s: erro do modelo: %s", e["id"], exc)
            if not dry_run:
                await banco.marcar(e["id"], "erro", f"modelo: {exc}", tent + 1)
        except Exception as exc:  # noqa: BLE001 - isolamento por edital
            resumo.erro += 1
            log.exception("edital %s: erro inesperado", e["id"])
            if not dry_run:
                await banco.marcar(e["id"], "erro", f"inesperado: {type(exc).__name__}", tent + 1)
    resumo.restantes = plano.elegiveis - resumo.processados
    return resumo


async def principal(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extrai critérios dos editais abertos (fila diária).")
    ap.add_argument("--dry-run", action="store_true", help="não grava nada no banco")
    ap.add_argument(
        "--max",
        type=int,
        default=None,
        help="máximo de editais (padrão: MAX_EDITAIS_POR_DIA ou 20)",
    )
    ap.add_argument("--edital", type=int, default=None, help="só este edital (id numérico)")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = ConfigSupabase.do_ambiente()
    if cfg is None:
        log.error("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY são obrigatórios")
        return 2
    banco = Banco(cfg)
    provedor = ProvedorGemini.do_ambiente()
    if provedor is None:
        log.warning("GEMINI_API_KEY ausente: só atualizo a fila")
    maximo = args.max if args.max is not None else max_editais_do_ambiente()
    try:
        async with ClienteWeb() as web:
            editais = None
            if args.edital is not None:
                editais = [x for x in await banco.editais_abertos() if x["id"] == args.edital]
                if not editais:
                    log.error("edital %s não está entre os abertos", args.edital)
                    return 2
            resumo = await executar(
                banco, web, provedor, max_editais=maximo, dry_run=args.dry_run, editais=editais
            )
    finally:
        await banco.fechar()
        if provedor:
            await provedor.fechar()
    for linha in resumo.linhas():
        print(linha)
    return 1 if resumo.interrompido and resumo.interrompido.startswith("config") else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
