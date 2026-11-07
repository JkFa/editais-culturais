import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from worker.modelos import Edital, Radar
from worker.persistencia import (
    ConfigSupabase,
    Supabase,
    editais_do_snapshot,
    escrever_snapshot,
    ler_snapshot,
    preservar_carimbos,
)

AGORA = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def edital(n: int, dias: int = 10, fonte: str = "mapas:teste") -> Edital:
    e = Edital(
        fonte=fonte,
        id_externo=str(n),
        url_origem=f"https://exemplo.invalid/{n}",
        titulo=f"Edital Teste {n}",
        orgao_fomento="Órgão Fictício",
        linguagens=["Música"],
        inscricao_fim=AGORA + timedelta(days=dias),
        coletado_em=AGORA,
        atualizado_em=AGORA,
    )
    return e.model_copy(update={"hash_conteudo": e.calcular_hash()})


def test_snapshot_so_abertos_e_ordenados(tmp_path):
    caminho = tmp_path / "data" / "snapshot.json"
    eds = [edital(1, 30), edital(2, -1), edital(3, 5)]
    radar = [Radar(id_externo="r", titulo="Radar Teste", orgao_fomento="X", linguagens=["Música"])]
    corpo = escrever_snapshot(caminho, eds, radar, ["b", "a"], [{"fonte": "c", "erro": "x"}], AGORA)
    lido = ler_snapshot(caminho)
    assert lido == json.loads(json.dumps(corpo))
    assert [e["id_externo"] for e in lido["editais"]] == ["3", "1"]
    assert lido["fontes_ok"] == ["a", "b"] and lido["fontes_falharam"][0]["fonte"] == "c"
    assert lido["editais"][0]["chave"] == "mapas:teste:3"
    assert lido["radar"][0]["id_externo"] == "r"
    assert not list(caminho.parent.glob("*.tmp"))


def test_snapshot_reidrata_editais(tmp_path):
    caminho = tmp_path / "s.json"
    escrever_snapshot(caminho, [edital(1)], [], ["x"], [], AGORA)
    voltou = editais_do_snapshot(ler_snapshot(caminho))
    assert voltou[0].id_externo == "1" and voltou[0].hash_conteudo == edital(1).hash_conteudo


def test_ler_snapshot_inexistente_ou_corrompido(tmp_path):
    assert ler_snapshot(tmp_path / "nao_existe.json") is None
    ruim = tmp_path / "ruim.json"
    ruim.write_text("{ nao e json")
    assert ler_snapshot(ruim) is None


def test_preservar_carimbos_quando_hash_igual():
    antigo = {
        ("mapas:teste", "1"): {
            "hash_conteudo": edital(1).hash_conteudo,
            "coletado_em": "2026-10-01T10:00:00+00:00",
            "atualizado_em": "2026-10-02T10:00:00+00:00",
        }
    }
    novo = preservar_carimbos([edital(1)], antigo)[0]
    assert novo.coletado_em.isoformat() == "2026-10-01T10:00:00+00:00"
    mudou = preservar_carimbos([edital(1).model_copy(update={"hash_conteudo": "outro"})], antigo)[0]
    assert mudou.coletado_em == AGORA


def supabase_falso(chamadas: list):
    def tratar(req: httpx.Request) -> httpx.Response:
        chamadas.append(req)
        if req.method == "GET" and req.url.path.endswith("/editais"):
            if "id_externo" in req.url.params.get("select", ""):
                return httpx.Response(200, json=[{"id_externo": "1"}, {"id_externo": "99"}])
            return httpx.Response(200, json=[])
        if req.method == "GET":
            return httpx.Response(200, json=[{"id_externo": "velho"}])
        return httpx.Response(201)

    return Supabase(
        ConfigSupabase("https://proj.supabase.invalid", "chave-de-teste"),
        transporte=httpx.MockTransport(tratar),
    )


async def test_upsert_em_lotes_de_100_com_chave_de_conflito():
    chamadas: list[httpx.Request] = []
    sb = supabase_falso(chamadas)
    n = await sb.upsert_editais([edital(i) for i in range(250)])
    await sb.fechar()
    posts = [c for c in chamadas if c.method == "POST"]
    assert n == 250 and [len(json.loads(c.content)) for c in posts] == [100, 100, 50]
    assert posts[0].url.params["on_conflict"] == "fonte,id_externo"
    assert "merge-duplicates" in posts[0].headers["Prefer"]
    assert posts[0].headers["apikey"] == "chave-de-teste"
    linha = json.loads(posts[0].content)[0]
    assert "chave" not in linha and "id" not in linha and "busca_textual" not in linha
    assert linha["fonte"] == "mapas:teste"


async def test_remover_sumidos_apaga_so_o_que_a_fonte_nao_devolve():
    chamadas: list[httpx.Request] = []
    sb = supabase_falso(chamadas)
    removidos = await sb.remover_sumidos("mapas:teste", {"1"}, AGORA)
    await sb.fechar()
    assert removidos == 1
    delete = next(c for c in chamadas if c.method == "DELETE")
    assert delete.url.params["id_externo"] == 'in.("99")'
    assert delete.url.params["fonte"] == "eq.mapas:teste"


async def test_heartbeat_radar_e_limpeza():
    chamadas: list[httpx.Request] = []
    sb = supabase_falso(chamadas)
    await sb.heartbeat(AGORA)
    await sb.sincronizar_radar(
        [Radar(id_externo="r1", titulo="Radar Teste", orgao_fomento="X")], AGORA
    )
    await sb.limpar_antigos()
    await sb.fechar()
    caminhos = [(c.method, c.url.path) for c in chamadas]
    assert ("POST", "/rest/v1/heartbeat") in caminhos
    assert ("POST", "/rest/v1/rpc/limpar_editais_antigos") in caminhos
    assert ("DELETE", "/rest/v1/radar_editais") in caminhos  # "velho" saiu do YAML


async def test_erro_4xx_do_banco_vira_excecao_sem_vazar_chave():
    sb = Supabase(
        ConfigSupabase("https://p.invalid", "segredo-xyz"),
        transporte=httpx.MockTransport(lambda r: httpx.Response(401, text="JWT inválido")),
    )
    with pytest.raises(RuntimeError) as exc:
        await sb.heartbeat(AGORA)
    await sb.fechar()
    assert "401" in str(exc.value) and "segredo-xyz" not in str(exc.value)


def test_config_do_ambiente(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert ConfigSupabase.do_ambiente() is None
    monkeypatch.setenv("SUPABASE_URL", "https://a.supabase.co/")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    assert ConfigSupabase.do_ambiente().url == "https://a.supabase.co"


async def test_chave_nova_sb_secret_vai_so_em_apikey():
    vistos = []

    def tratar(req):
        vistos.append(req)
        return httpx.Response(201)

    sb = Supabase(
        ConfigSupabase("https://p.invalid", "sb_secret_de-teste"),
        transporte=httpx.MockTransport(tratar),
    )
    await sb.heartbeat(AGORA)
    await sb.fechar()
    assert vistos[0].headers["apikey"] == "sb_secret_de-teste"
    assert "authorization" not in vistos[0].headers
