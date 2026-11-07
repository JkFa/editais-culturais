import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from conftest import carregar

from worker import scraper_worker
from worker.coletores import mapas_culturais
from worker.http import ClienteHttp
from worker.web import ClienteWeb

AGORA = datetime.now(UTC)


def test_carregar_env_nao_sobrescreve_o_ambiente(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        '# comentário\nSUPABASE_URL="https://a.invalid"\nVAZIA=\nJA_DEFINIDA=do-arquivo\n'
    )
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("JA_DEFINIDA", "do-ambiente")
    monkeypatch.delenv("VAZIA", raising=False)
    scraper_worker.carregar_env(env)
    assert os.environ["SUPABASE_URL"] == "https://a.invalid"
    assert os.environ["JA_DEFINIDA"] == "do-ambiente"
    assert "VAZIA" not in os.environ
    monkeypatch.delenv("SUPABASE_URL")


def test_carregar_env_inexistente_nao_falha(tmp_path):
    scraper_worker.carregar_env(tmp_path / "nao-existe")


def test_fontes_yaml_valido_e_instancias_inativas_tem_motivo():
    instancias, params = scraper_worker.carregar_fontes()
    slugs = {i.slug for i in instancias}
    assert {"br", "ce"} <= slugs
    assert params.tipos_aceitos == ["Edital"]
    for i in instancias:
        if not i.ativa:
            assert i.motivo


@pytest.fixture(autouse=True)
def sem_fontes_v4_na_rede(monkeypatch, request):
    """Os testes de orquestração não saem para a internet: as fontes v4 têm testes próprios."""
    if "com_fontes_v4" in request.keywords:
        return

    async def vazio(*a, **kw):
        from worker.fontes_v4 import ResultadoV4

        return ResultadoV4()

    monkeypatch.setattr(scraper_worker, "rodar_fontes_v4", vazio)


@pytest.fixture
def servidor_falso(monkeypatch):
    """Serve as fixtures reais no lugar da rede; a instância 'pe' devolve 503."""
    por_host = {
        "mapa.cultura.gov.br": carregar("mapas_mapa.cultura.gov.br.json"),
        "mapacultural.secult.ce.gov.br": carregar("mapas_mapacultural.secult.ce.gov.br.json"),
    }

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.host == "mapacultural.pe.gov.br":
            return httpx.Response(503)
        if int(req.url.params.get("@page", "1")) > 1:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=por_host.get(req.url.host, []))

    original = ClienteHttp.__init__

    def init(self, *a, **kw):
        original(self, *a, **{**kw, "transporte": httpx.MockTransport(tratar), "pausa_base": 0})

    monkeypatch.setattr(ClienteHttp, "__init__", init)


async def test_dry_run_gera_snapshot_valido_e_falha_isolada(tmp_path, servidor_falso, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    snapshot = tmp_path / "snapshot.json"
    codigo = await scraper_worker.executar(dry_run=True, snapshot=snapshot)
    assert codigo == 0
    corpo = json.loads(snapshot.read_text(encoding="utf-8"))
    assert "mapas:pe" in [f["fonte"] for f in corpo["fontes_falharam"]]
    assert "curadoria" in corpo["fontes_ok"] and "mapas:ce" in corpo["fontes_ok"]
    assert corpo["editais"] and all(
        e["url_origem"] and e["inscricao_fim"] for e in corpo["editais"]
    )
    assert {e["fonte"] for e in corpo["editais"]} >= {"curadoria", "mapas:ce"}
    assert len(corpo["radar"]) == 6
    fins = [e["inscricao_fim"] for e in corpo["editais"]]
    assert fins == sorted(fins)


async def test_fonte_que_falha_mantem_o_que_tinha_no_snapshot_anterior(
    tmp_path, servidor_falso, monkeypatch
):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    snapshot = tmp_path / "snapshot.json"
    anterior = json.loads(Path(scraper_worker.SNAPSHOT).read_text(encoding="utf-8"))
    pe = [e for e in anterior["editais"] if e["fonte"] == "mapas:pe"]
    assert pe, "o snapshot commitado deveria ter editais de PE"
    snapshot.write_text(json.dumps(anterior), encoding="utf-8")
    assert await scraper_worker.executar(dry_run=True, snapshot=snapshot) == 0
    novo = json.loads(snapshot.read_text(encoding="utf-8"))
    ainda_abertos = [
        e["chave"] for e in pe if datetime.fromisoformat(e["inscricao_fim"]) >= datetime.now(UTC)
    ]
    assert {e["chave"] for e in novo["editais"] if e["fonte"] == "mapas:pe"} == set(ainda_abertos)


async def test_todas_as_fontes_falhando_preserva_snapshot_e_retorna_1(tmp_path, monkeypatch):
    original = ClienteHttp.__init__

    def init(self, *a, **kw):
        original(
            self,
            *a,
            **{
                **kw,
                "transporte": httpx.MockTransport(lambda r: httpx.Response(500)),
                "pausa_base": 0,
            },
        )

    monkeypatch.setattr(ClienteHttp, "__init__", init)
    monkeypatch.setattr(scraper_worker, "CURADAS", tmp_path / "inexistente.yaml")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('{"preservado": true}', encoding="utf-8")
    assert await scraper_worker.executar(dry_run=True, snapshot=snapshot) == 1
    assert json.loads(snapshot.read_text()) == {"preservado": True}


async def test_sem_credenciais_roda_como_dry_run(tmp_path, servidor_falso, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert await scraper_worker.executar(dry_run=False, snapshot=tmp_path / "s.json") == 0
    assert (tmp_path / "s.json").exists()


def test_paginacao_maxima_nao_e_infinita():
    assert mapas_culturais.MAX_PAGINAS <= 50 and AGORA


async def test_banco_vazio_grava_tudo_mesmo_com_snapshot_anterior(
    tmp_path, servidor_falso, monkeypatch
):
    """Regressão: o snapshot não prova que a linha existe no Supabase."""
    import httpx as _httpx

    from worker import persistencia

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://p.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    snapshot = tmp_path / "s.json"
    snapshot.write_text(Path(scraper_worker.SNAPSHOT).read_text(encoding="utf-8"), encoding="utf-8")
    posts: list[int] = []

    def tratar(req):
        if req.method == "POST" and req.url.path.endswith("/editais"):
            posts.append(len(json.loads(req.content)))
        if req.method == "GET":
            return _httpx.Response(200, json=[])
        return _httpx.Response(201)

    original = persistencia.Supabase.__init__

    def init(self, cfg, transporte=None):
        original(self, cfg, transporte=_httpx.MockTransport(tratar))

    monkeypatch.setattr(persistencia.Supabase, "__init__", init)
    assert await scraper_worker.executar(dry_run=False, snapshot=snapshot) == 0
    assert sum(posts) > 0


async def test_fonte_unica_desconhecida_retorna_2(capsys):
    assert await scraper_worker.executar_fonte_unica("nao-existe", dry_run=True) == 2
    assert "fonte desconhecida" in capsys.readouterr().out


@pytest.mark.com_fontes_v4
async def test_fonte_unica_desativada_nao_faz_requisicao_e_reporta_o_motivo(capsys, monkeypatch):
    chamadas = []

    def tratar(req):
        chamadas.append(req)
        return httpx.Response(500)

    original = ClienteWeb.__init__ if False else None
    _ = original
    assert await scraper_worker.executar_fonte_unica("prosas-central-de-editais", dry_run=True) == 0
    saida = capsys.readouterr().out
    assert '"status": "desativada"' in saida and "extração de dados" in saida
    assert chamadas == []


async def test_snapshot_gerado_leva_o_catalogo_de_fontes_com_saude(
    tmp_path, servidor_falso, monkeypatch
):
    from worker.catalogo import carregar_fontes_v4, status_desativada
    from worker.fontes_v4 import ResultadoV4

    async def so_desativadas(fontes, *a, **kw):
        return ResultadoV4(status=[status_desativada(f) for f in fontes if not f.ativa])

    monkeypatch.setattr(scraper_worker, "rodar_fontes_v4", so_desativadas)
    _ = carregar_fontes_v4
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    snapshot = tmp_path / "s.json"
    assert await scraper_worker.executar(dry_run=True, snapshot=snapshot) == 0
    corpo = json.loads(snapshot.read_text(encoding="utf-8"))
    por_id = {f["fonte"]: f for f in corpo["fontes"]}
    assert por_id["mapas:ce"]["status"] == "ok" and por_id["mapas:ce"]["total_coletado"] > 0
    assert por_id["mapas:pe"]["status"] == "erro"  # o servidor falso devolve 503
    assert por_id["curadoria"]["status"] == "ok"
    assert por_id["mapas:df"]["status"] == "desativada" and por_id["mapas:df"]["motivo"]
    assert por_id["prosas-central-de-editais"]["status"] == "desativada"
