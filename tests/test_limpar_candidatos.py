"""Limpeza semanal dos candidatos com prazo vencido."""

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import yaml

from scripts import limpar_candidatos_vencidos as lc
from worker.fila import FilaGitHub

RAIZ = Path(__file__).resolve().parent.parent
HOJE = date(2026, 11, 8)


def issue(n, prazo, rotulos=("candidato",), corpo_extra=""):
    yml = f"```yaml\ntitulo: x\ninscricao_fim: {prazo}\n```" if prazo else "```yaml\ntitulo: x\n```"
    return {
        "number": n,
        "title": f"Candidato {n}",
        "labels": [{"name": r} for r in rotulos],
        "body": f"**Link oficial:** https://x\n{yml}{corpo_extra}",
    }


def fila(issues, falhar=()):
    chamadas = []

    def tratar(req: httpx.Request) -> httpx.Response:
        corpo = json.loads(req.content) if req.content else None
        chamadas.append((req.method, req.url.path, corpo))
        if req.method == "GET":
            return httpx.Response(200, json=issues if req.url.params.get("page") == "1" else [])
        if req.method == "PATCH" and any(req.url.path.endswith(f"/{n}") for n in falhar):
            return httpx.Response(500)
        return httpx.Response(201 if req.method == "POST" else 200, json={})

    return FilaGitHub("o/r", "t", transporte=httpx.MockTransport(tratar)), chamadas


def rodar(issues, dry_run=False, falhar=()):
    f, chamadas = fila(issues, falhar)

    async def corrida():
        try:
            return await lc.limpar(f, HOJE, dry_run)
        finally:
            await f.fechar()

    return asyncio.run(corrida()), chamadas


def test_fecha_so_o_que_venceu_com_margem_e_comenta_como_nao_planejada():
    issues = [
        issue(1, "2026-10-01"),  # vencida há 38 dias
        issue(2, "2026-11-06"),  # vencida há 2 dias: no limite da margem
        issue(3, "2026-11-07"),  # há 1 dia: ainda pode ser o bug do prazo adiantado
        issue(4, "2026-11-20"),  # futura
        issue(5, None),  # sem prazo no YAML
        issue(6, "2026-01-01", rotulos=("candidato", "aprovado")),  # aprovada: nunca
    ]
    fechadas, chamadas = rodar(issues)
    assert fechadas == [1, 2]
    patches = [c for c in chamadas if c[0] == "PATCH"]
    assert [p[1].rsplit("/", 1)[-1] for p in patches] == ["1", "2"]
    assert all(p[2] == {"state": "closed", "state_reason": "not_planned"} for p in patches), (
        "não planejada: não conta como aprovada"
    )
    comentario = next(
        c[2]["body"] for c in chamadas if c[0] == "POST" and c[1].endswith("/issues/1/comments")
    )
    assert (
        "01/10/2026" in comentario
        and "prazo de inscrição" in comentario
        and "novo candidato" in comentario
    )


def test_dry_run_so_lista_e_nao_escreve():
    fechadas, chamadas = rodar([issue(1, "2026-10-01")], dry_run=True)
    assert fechadas == [1] and not [c for c in chamadas if c[0] in ("POST", "PATCH")]


def test_falha_em_uma_issue_nao_impede_as_outras():
    fechadas, _ = rodar(
        [issue(1, "2026-10-01"), issue(2, "2026-10-02"), issue(3, "2026-10-03")], falhar=(2,)
    )
    assert fechadas == [1, 3]


def test_prazo_em_formatos_do_yaml_e_lixo():
    assert lc.prazo_da_issue(issue(1, "2026-10-01")) == date(2026, 10, 1)
    assert lc.prazo_da_issue(issue(1, "'2026-10-01T23:59:59'")) == date(2026, 10, 1)
    assert lc.prazo_da_issue(issue(1, "'não é data'")) is None
    assert lc.prazo_da_issue({"body": "sem yaml"}) is None


def test_limite_por_execucao(monkeypatch):
    monkeypatch.setattr(lc, "MAXIMO_POR_EXECUCAO", 2)
    fechadas, _ = rodar([issue(n, f"2026-09-{n:02d}") for n in range(1, 6)])
    assert fechadas == [1, 2], "os mais antigos primeiro"


def test_cli_sem_token_nao_faz_nada(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert asyncio.run(lc.principal([])) == 0 and "nada a fazer" in capsys.readouterr().out


def test_fechar_issue_antigo_continua_fechando_como_concluida_para_o_fluxo_de_aprovadas():
    f, chamadas = fila([])
    asyncio.run(f.fechar_issue(7, "Prazo encerrado"))
    assert [c[2] for c in chamadas if c[0] == "PATCH"] == [
        {"state": "closed", "state_reason": "completed"}
    ]


def test_workflow_semanal_com_permissao_minima():
    texto = (RAIZ / ".github" / "workflows" / "limpar_candidatos.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(texto)
    assert wf[True]["schedule"][0]["cron"] == "0 12 * * 1" and "workflow_dispatch" in wf[True]
    assert wf["permissions"] == {"issues": "write"}, "só Issues: não escreve no repositório"
    assert "python -m scripts.limpar_candidatos_vencidos" in texto
