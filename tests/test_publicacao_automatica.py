"""Publicação automática (PR B): o edital sai com selo honesto, rótulos nas Issues, chave liga/desliga e integração na coleta."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml
from conftest import carregar  # noqa: F401  (garante o sys.path dos testes)

from worker import automatica as a
from worker import scraper_worker
from worker.coletores.curadoria import EditalCurado
from worker.fila import ROTULO_AUTO, FilaGitHub
from worker.modelos import Edital
from worker.padronizacao import padronizar
from worker.web import ClienteWeb

RAIZ = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures" / "automatica"
ORIGEM = json.loads((FIX / "ORIGEM.json").read_text(encoding="utf-8"))["itens"]
HOJE = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def decisao(nome="gurupi"):
    m = ORIGEM[nome]
    d = a.avaliar(
        m["numero"],
        m["titulo_issue"],
        m["link"],
        (FIX / f"{nome}.txt").read_text(encoding="utf-8"),
        HOJE,
        a.carregar_dominios(),
    )
    assert d.publica, d.motivos
    return d


# ── o edital publicado ───────────────────────────────────────────────────────────────────────


def test_bruto_do_candidato_leva_o_selo_e_nunca_afirma_conferencia_humana():
    b = a.bruto_do_candidato(decisao("gurupi"), HOJE)
    assert (
        b.fonte == "publicacao-automatica"
        and b.verificacao == "automatica"
        and b.id_externo == "auto-80"
    )
    assert b.verificado_em is None, "verificado_em diria que uma pessoa conferiu"
    assert b.via is None and b.orgao_fomento == "Prefeitura Municipal de Gurupi" and b.uf == "TO"
    assert {p.via for p in b.proveniencia.values()} == {"regex"}, (
        "nenhuma proveniência pode dizer 'curadoria'"
    )
    assert (
        b.proveniencia["inscricao_fim"].trecho and "gurupi.to.gov.br" in b.proveniencia["uf"].trecho
    )
    assert b.linguagens == ["Multiáreas"] and b.inscricao_fim > HOJE


def test_edital_padronizado_mantem_o_selo_e_a_curadoria_humana_tem_o_seu():
    e = padronizar(a.bruto_do_candidato(decisao("tanabi"), HOJE), HOJE)
    assert (
        e.verificacao == "automatica"
        and e.verificado_em is None
        and e.fonte == "publicacao-automatica"
    )
    curado = EditalCurado.model_validate({
        "id_externo": "x", "titulo": "Edital", "orgao_fomento": "Org", "url_origem": "https://x.gov.br", "resumo_simples": "r",
        "linguagens": ["Música"], "inscricao_fim": "2099-01-01", "verificado_em": "2026-11-01", "evidencias": {"inscricao_fim": "até 01/01/2099"},
    }).para_bruto()  # fmt: skip
    assert curado.verificacao == "curadoria"


def test_hash_dos_editais_sem_selo_nao_muda_com_o_campo_novo():
    """Senão a coleta regravaria todos os editais só por causa da coluna nova."""
    e = Edital(
        fonte="mapas:ce",
        id_externo="1",
        url_origem="https://x",
        titulo="t",
        orgao_fomento="o",
        inscricao_fim=HOJE,
    )
    com_chave = e.para_linha()
    assert "verificacao" in com_chave and com_chave["verificacao"] is None
    h = e.calcular_hash()
    outro = e.model_copy(update={"verificacao": "automatica"})
    assert outro.calcular_hash() != h, "o selo muda o conteúdo"
    sem = {
        k: v
        for k, v in com_chave.items()
        if k not in ("coletado_em", "atualizado_em", "hash_conteudo", "verificacao")
    }
    assert "verificacao" not in sem  # o hash antigo nunca teve o campo


def test_chave_liga_desliga_so_liga_com_valor_explicito(monkeypatch):
    for v, esperado in [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("sim", True),
        ("", False),
        ("false", False),
        ("talvez", False),
    ]:
        monkeypatch.setenv("PUBLICACAO_AUTOMATICA", v)
        assert a.ligada() is esperado, v
    monkeypatch.delenv("PUBLICACAO_AUTOMATICA")
    assert a.ligada() is False, "desligada por padrão"


# ── Issues: rótulo, comentário e veto ────────────────────────────────────────────────────────


def fila_falsa(issues):
    chamadas = []

    def tratar(req: httpx.Request) -> httpx.Response:
        corpo = json.loads(req.content) if req.content else None
        chamadas.append((req.method, req.url.path, corpo))
        if req.method == "GET":
            return httpx.Response(200, json=issues if req.url.params.get("page") == "1" else [])
        return httpx.Response(201 if req.method == "POST" else 200, json=[])

    return FilaGitHub("o/r", "t", transporte=httpx.MockTransport(tratar)), chamadas


def issue(n, link, rotulos=("candidato",)):
    return {
        "number": n,
        "title": f"Candidato: {ORIGEM['gurupi']['titulo_issue']}",
        "labels": [{"name": r} for r in rotulos],
        "body": f"**Link oficial:** {link}\n",
    }


def web_com(paginas):
    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/robots.txt"):
            return httpx.Response(404)
        r = paginas.get(str(req.url))
        return (
            httpx.Response(404)
            if r is None
            else httpx.Response(200, text=r, headers={"content-type": "text/html"})
        )

    async def sem_espera(_):
        return None

    return ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera)


def html_do(nome):
    return (
        "<html><body><main><p>"
        + (FIX / f"{nome}.txt").read_text(encoding="utf-8")
        + "</p></main></body></html>"
    )


def rodar_publicar(issues, paginas):
    fila, chamadas = fila_falsa(issues)

    async def corrida():
        async with web_com(paginas) as web:
            try:
                return await a.publicar_automaticos(fila, web, HOJE)
            finally:
                await fila.fechar()

    return asyncio.run(corrida()), chamadas


LINK_OK = "https://gurupi.to.gov.br/edital-poesia/"


def test_publica_rotula_a_issue_uma_vez_e_nao_repete_na_proxima_coleta():
    (brutos, ds), chamadas = rodar_publicar([issue(80, LINK_OK)], {LINK_OK: html_do("gurupi")})
    assert [b.id_externo for b in brutos] == ["auto-80"] and ds[0].publica
    posts = [c for c in chamadas if c[0] == "POST"]
    assert any(
        c[1].endswith("/issues/80/labels") and c[2] == {"labels": [ROTULO_AUTO]} for c in posts
    )
    assert any(
        "automaticamente" in (c[2] or {}).get("body", "") and "nao-publicar" in c[2]["body"]
        for c in posts
    )
    (brutos2, _), chamadas2 = rodar_publicar(
        [issue(80, LINK_OK, ("candidato", ROTULO_AUTO))], {LINK_OK: html_do("gurupi")}
    )
    assert [b.id_externo for b in brutos2] == ["auto-80"]
    assert not [c for c in chamadas2 if c[0] in ("POST", "DELETE")], (
        "já rotulada: nada de rótulo nem comentário repetidos"
    )


def test_deixou_de_valer_sai_do_ar_e_a_issue_recebe_o_motivo():
    (brutos, ds), chamadas = rodar_publicar(
        [issue(80, LINK_OK, ("candidato", ROTULO_AUTO))],
        {LINK_OK: "<html><body>Carregando...</body></html>"},
    )
    assert brutos == [] and not ds[0].publica
    assert any(
        c[0] == "DELETE" and c[1].endswith(f"/issues/80/labels/{ROTULO_AUTO}") for c in chamadas
    )
    comentario = next(
        c[2]["body"] for c in chamadas if c[0] == "POST" and c[1].endswith("/comments")
    )
    assert "Saiu do ar automaticamente" in comentario and "sem texto legível" in comentario


def test_veto_humano_nunca_publica_e_tira_o_que_estava_no_ar():
    (brutos, ds), chamadas = rodar_publicar(
        [issue(80, LINK_OK, ("candidato", ROTULO_AUTO, "nao-publicar"))],
        {LINK_OK: html_do("gurupi")},
    )
    assert brutos == [] and "veto humano" in ds[0].motivos[0]
    assert any(c[0] == "DELETE" for c in chamadas)


def test_candidato_que_a_curadoria_rejeita_continua_na_fila_humana(monkeypatch):
    def quebra(d, hoje):
        raise ValueError("campo inválido")

    monkeypatch.setattr(a, "bruto_do_candidato", quebra)
    (brutos, ds), _ = rodar_publicar([issue(80, LINK_OK)], {LINK_OK: html_do("gurupi")})
    assert brutos == [] and not ds[0].publica and "validação da curadoria" in ds[0].motivos[-1]


# ── integração na coleta ─────────────────────────────────────────────────────────────────────


class FilaDeTeste:
    """Só o que a coleta usa da fila; os candidatos publicados vêm de `publicar_automaticos` (trocado nos testes)."""

    async def garantir_rotulos(self):
        pass

    async def marcadores_existentes(self):
        return set()

    async def aprovados(self):
        return []

    async def criar(self, c):
        return 1

    async def fechar(self):
        pass


@pytest.fixture
def coleta_isolada(monkeypatch):
    from worker.fontes_v4 import ResultadoV4

    async def vazio(*args, **kwargs):
        return ResultadoV4()

    def tratar(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[]) if "@page" in req.url.params else httpx.Response(404)

    from worker.http import ClienteHttp

    original = ClienteHttp.__init__
    monkeypatch.setattr(
        ClienteHttp,
        "__init__",
        lambda self, *a, **kw: original(
            self, *a, **{**kw, "transporte": httpx.MockTransport(tratar), "pausa_base": 0}
        ),
    )
    monkeypatch.setattr(scraper_worker, "rodar_fontes_v4", vazio)
    monkeypatch.setattr(scraper_worker, "_fila_do_ambiente", lambda dry_run: FilaDeTeste())
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_coleta_com_a_chave_desligada_nao_publica_e_mostra_a_fonte_desativada(
    tmp_path, monkeypatch, coleta_isolada
):
    monkeypatch.delenv("PUBLICACAO_AUTOMATICA", raising=False)
    chamou = []

    async def nao_deve(*args, **kw):
        chamou.append(1)
        return [], []

    monkeypatch.setattr(a, "publicar_automaticos", nao_deve)
    snap = tmp_path / "s.json"
    assert asyncio.run(scraper_worker.executar(dry_run=True, snapshot=snap)) == 0
    corpo = json.loads(snap.read_text(encoding="utf-8"))
    fonte = next(f for f in corpo["fontes"] if f["fonte"] == "publicacao-automatica")
    assert fonte["status"] == "desativada" and "Desligada" in fonte["motivo"] and not fonte["ativa"]
    assert not chamou and not [e for e in corpo["editais"] if e["fonte"] == "publicacao-automatica"]


def test_coleta_com_a_chave_ligada_publica_com_selo_e_preserva_em_falha(
    tmp_path, monkeypatch, coleta_isolada
):
    monkeypatch.setenv("PUBLICACAO_AUTOMATICA", "true")
    bruto = a.bruto_do_candidato(decisao("gurupi"), datetime.now(UTC))
    bruto = bruto.model_copy(update={"inscricao_fim": datetime(2099, 1, 1, tzinfo=UTC)})

    async def publica(fila, web, hoje, aprovados=None):
        return [bruto], []

    monkeypatch.setattr(a, "publicar_automaticos", publica)
    snap = tmp_path / "s.json"
    assert asyncio.run(scraper_worker.executar(dry_run=True, snapshot=snap)) == 0
    corpo = json.loads(snap.read_text(encoding="utf-8"))
    e = next(x for x in corpo["editais"] if x["fonte"] == "publicacao-automatica")
    assert (
        e["verificacao"] == "automatica"
        and e["verificado_em"] is None
        and e["orgao_fomento"] == "Prefeitura Municipal de Gurupi"
    )
    fonte = next(f for f in corpo["fontes"] if f["fonte"] == "publicacao-automatica")
    assert fonte["status"] == "ok" and fonte["ativa"] and fonte["total_coletado"] == 1
    # a fila do GitHub cai: o que estava publicado continua no ar (preservado do snapshot anterior)

    async def cai(fila, web, hoje, aprovados=None):
        raise httpx.ConnectError("fila fora")

    monkeypatch.setattr(a, "publicar_automaticos", cai)
    assert asyncio.run(scraper_worker.executar(dry_run=True, snapshot=snap)) == 0
    depois = json.loads(snap.read_text(encoding="utf-8"))
    assert [
        x["id_externo"] for x in depois["editais"] if x["fonte"] == "publicacao-automatica"
    ] == ["auto-80"]
    assert any(f["fonte"] == "publicacao-automatica" for f in depois["fontes_falharam"])


def test_publicado_sem_conferencia_perde_para_qualquer_outra_origem_do_mesmo_edital():
    from worker.dedup import deduplicar

    base = {
        "url_origem": "https://x.gov.br/a",
        "titulo": "Edital de Fomento Cultural Cearense",
        "orgao_fomento": "Secult",
        "inscricao_fim": datetime(2099, 1, 1, tzinfo=UTC),
    }
    auto = Edital(fonte="publicacao-automatica", id_externo="auto-1", **base)
    mapas = Edital(fonte="mapas:ce", id_externo="9", **{**base, "url_origem": "https://x.gov.br/a"})
    saida, removidos = deduplicar(
        [auto, mapas], {"publicacao-automatica": "descoberta", "mapas:ce": "plataforma"}
    )
    assert removidos == 1 and saida[0].fonte == "mapas:ce"
    assert (
        saida[0].fontes_alternativas
        and saida[0].fontes_alternativas[0]["fonte"] == "publicacao-automatica"
    )


# ── arquivos de configuração ─────────────────────────────────────────────────────────────────


def test_workflow_passa_a_chave_da_variavel_do_repositorio_e_nao_a_liga_sozinho():
    texto = (RAIZ / ".github" / "workflows" / "sync_editais.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(texto)
    passo = next(p for p in wf["jobs"]["sync"]["steps"] if p.get("name") == "Coletar editais")
    assert passo["env"]["PUBLICACAO_AUTOMATICA"] == "${{ vars.PUBLICACAO_AUTOMATICA }}"
    assert (
        "PUBLICACAO_AUTOMATICA: true" not in texto and "PUBLICACAO_AUTOMATICA: 'true'" not in texto
    )


def test_migration_cria_coluna_com_check_e_recria_a_view():
    sql = (RAIZ / "supabase" / "migrations" / "20261111000000_verificacao.sql").read_text(
        encoding="utf-8"
    )
    assert (
        "add column if not exists verificacao text" in sql and "('curadoria', 'automatica')" in sql
    )
    assert "create or replace view public.editais_abertos" in sql
    assert sql.strip() in (RAIZ / "supabase" / "schema.sql").read_text(encoding="utf-8"), (
        "schema.sql tem que acompanhar a migration"
    )
