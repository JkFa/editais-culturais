"""Curadoria internacional: schema validado, regras de integridade e nenhuma entrada fictícia."""

import copy
import json
from datetime import date
from pathlib import Path

import jsonschema
import pytest

from worker.coletores import curadoria_internacional as ci
from worker.padronizacao import padronizar

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "worker" / "curadoria_internacional.json"
SCHEMA = RAIZ / "worker" / "curadoria_internacional.schema.json"
HOJE = date(2026, 11, 7)


def carregar_json(caminho: Path):
    return json.loads(caminho.read_text(encoding="utf-8"))


@pytest.fixture
def entrada():
    return copy.deepcopy(carregar_json(DADOS)["oportunidades"][0])


def escrever(tmp_path, *entradas):
    arq = tmp_path / "c.json"
    arq.write_text(json.dumps({"oportunidades": list(entradas)}), encoding="utf-8")
    return arq


def test_schema_e_valido_e_o_arquivo_de_dados_passa_nele():
    schema = carregar_json(SCHEMA)
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(carregar_json(DADOS), schema)


def test_nenhuma_entrada_ficticia_nem_sem_evidencia():
    """Regra editorial: só o que foi aberto e conferido na página oficial, com prazo futuro na conferência.

    O prazo é comparado com a data da conferência (e não com "hoje"): quando uma chamada vence, a
    coleta a tira sozinha e este teste não pode quebrar por causa disso.
    """
    dados = carregar_json(DADOS)
    assert dados["oportunidades"], "se nada foi confirmado, o arquivo deve ficar só com o schema"
    for op in dados["oportunidades"]:
        conferido = date.fromisoformat(op["conferido_em"])
        resultado = ci.carregar(DADOS, hoje=conferido)
        assert op["id"] in {b.id_externo for b in resultado.editais}, (
            f"{op['id']}: vencida na conferência"
        )
        assert op["id"] != "exemplo-nao-publicar"
        assert op["url_oficial"].startswith("https://")
        assert not any(h in op["url_oficial"] for h in ("example", "exemplo", ".invalid"))
        assert op["evidencias"]["prazo_original"]  # trecho literal da página oficial
        assert conferido <= date.today()


def test_camera_libre_entra_como_verificar_com_custo_e_evidencia():
    b = next(x for x in ci.carregar(DADOS, HOJE).editais if x.id_externo.startswith("camera-libre"))
    assert (
        b.elegibilidade_br == "verificar" and b.pais_destino == "FR" and b.idioma_original == "fr"
    )
    assert b.tipo_oportunidade == "residencia" and b.linguagens == ["Cinema & Audiovisual"]
    assert (
        b.custos_cobertos == ["viagem"]
        and "venue en France" in b.proveniencia["custos_cobertos"].trecho
    )
    # 16/11/2026, só data: vale até 23:59:59 de Brasília
    assert b.inscricao_fim.isoformat() == "2026-11-17T02:59:59+00:00" and b.fim_tem_horario is False
    assert "16 novembre 2026" in b.proveniencia["inscricao_fim"].trecho
    assert b.url_origem.startswith("https://www.cnc.fr/")


def test_exemplo_comentado_nao_vira_oportunidade_e_nao_e_lido():
    dados = carregar_json(DADOS)
    assert "exemplo_comentado" in dados
    ids = {b.id_externo for b in ci.carregar(DADOS, HOJE).editais}
    assert "exemplo-nao-publicar" not in ids


def test_ibermuseos_vira_edital_internacional_com_prazo_e_fonte_da_curadoria():
    res = ci.carregar(DADOS, HOJE)
    assert [b.id_externo for b in res.editais] == [
        "ibermuseos-fondo-7-2026",
        "camera-libre-2026-2a-sessao",
    ]
    b = res.editais[0]
    assert b.fonte == "curadoria-internacional" and b.via == "curadoria do portal"
    assert b.ambito == "internacional" and b.pais_destino == "Ibero-América"
    assert b.elegibilidade_br == "aberto" and "Brasil" in b.proveniencia["elegibilidade_br"].trecho
    assert b.fim_tem_horario is False and b.inscricao_fim.isoformat() == "2027-03-06T02:59:59+00:00"
    assert "5 de marzo de 2027" in b.proveniencia["inscricao_fim"].trecho
    edital = padronizar(b, HOJE_DT)
    assert edital.resumo_simples == b.resumo_simples and len(edital.resumo_simples) <= 280
    assert edital.linguagens == ["Patrimônio"] and edital.moeda == "EUR"


from datetime import UTC, datetime  # noqa: E402

HOJE_DT = datetime(2026, 11, 7, tzinfo=UTC)


@pytest.mark.parametrize(
    "mudanca, trecho_do_erro",
    [
        (lambda e: e.pop("evidencias") and None, "evidencias"),
        (lambda e: e["evidencias"].pop("elegibilidade_br"), "elegibilidade"),
        (lambda e: e.update(custos_cobertos=["viagem"]), "custos_cobertos"),
        (lambda e: e.update(url_oficial="https://exemplo.invalid/x"), "exemplo"),
        (lambda e: e.update(url_oficial="http://www.ibermuseos.org/x"), "url_oficial"),
        (lambda e: e.update(tipo_oportunidade="sorteio"), "tipo_oportunidade"),
        (lambda e: e.update(linguagens=["Gastronomia"]), "taxonomia"),
        (lambda e: e.update(observacao_curta="curta"), "observacao_curta"),
    ],
)
def test_entrada_invalida_derruba_a_fonte_com_erro_explicito(
    tmp_path, entrada, mudanca, trecho_do_erro
):
    mudanca(entrada)
    with pytest.raises(ci.ErroCuradoriaInternacional, match=trecho_do_erro):
        ci.carregar(escrever(tmp_path, entrada), HOJE)


def test_schema_tambem_recusa_aberto_sem_evidencia(entrada):
    schema = carregar_json(SCHEMA)
    entrada["evidencias"].pop("elegibilidade_br")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"oportunidades": [entrada]}, schema)


def test_id_duplicado_e_recusado(tmp_path, entrada):
    with pytest.raises(ci.ErroCuradoriaInternacional, match="duplicado"):
        ci.carregar(escrever(tmp_path, entrada, entrada), HOJE)


def test_prazo_vencido_nao_publica_e_conferencia_antiga_avisa(tmp_path, entrada):
    res = ci.carregar(escrever(tmp_path, entrada), date(2027, 3, 7))
    assert res.editais == [] and "vencido" in res.avisos[0]
    antiga = copy.deepcopy(entrada) | {"conferido_em": "2026-08-01"}
    res = ci.carregar(escrever(tmp_path, antiga), HOJE)
    assert len(res.editais) == 1 and "reconfira" in res.avisos[0]


def test_prazo_com_horario_e_fuso_preserva_o_que_a_fonte_informa(tmp_path, entrada):
    entrada |= {"prazo_original": "2027-03-05T17:00:00+01:00", "prazo_fuso": "Europe/Brussels"}
    entrada["evidencias"]["prazo_original"] = "Deadline: 5 March 2027, 17:00 (Brussels time)"
    b = ci.carregar(escrever(tmp_path, entrada), HOJE).editais[0]
    assert b.fim_tem_horario is True and b.prazo_fuso == "Europe/Brussels"
    assert b.inscricao_fim.isoformat() == "2027-03-05T16:00:00+00:00"
    edital = padronizar(b, HOJE_DT)
    # o instante é o da fonte (17h em Bruxelas); `prazo_fuso` diz em que relógio exibir
    assert edital.prazo_original == datetime.fromisoformat("2027-03-05T17:00:00+01:00")
    assert edital.prazo_fuso == "Europe/Brussels"


async def test_curadoria_internacional_quebrada_nao_derruba_a_coleta_brasileira(
    tmp_path, monkeypatch
):
    """JSON inválido vira falha só da fonte `curadoria-internacional`; o resto da coleta segue."""
    import httpx

    from worker import scraper_worker
    from worker.http import ClienteHttp

    original = ClienteHttp.__init__

    def init(self, *a, **kw):
        original(
            self,
            *a,
            **{
                **kw,
                "transporte": httpx.MockTransport(lambda r: httpx.Response(200, json=[])),
                "pausa_base": 0,
            },
        )

    async def sem_v4(*a, **kw):
        from worker.fontes_v4 import ResultadoV4

        return ResultadoV4()

    quebrado = tmp_path / "quebrado.json"
    quebrado.write_text("{ isto não é json", encoding="utf-8")
    monkeypatch.setattr(ClienteHttp, "__init__", init)
    monkeypatch.setattr(scraper_worker, "rodar_fontes_v4", sem_v4)
    monkeypatch.setattr(scraper_worker, "CURADAS_INTERNACIONAL", quebrado)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    snapshot = tmp_path / "s.json"
    assert await scraper_worker.executar(dry_run=True, snapshot=snapshot) == 0
    corpo = json.loads(snapshot.read_text(encoding="utf-8"))
    assert [f["fonte"] for f in corpo["fontes_falharam"]] == ["curadoria-internacional"]
    assert "curadoria" in corpo["fontes_ok"]
    assert any(
        f["fonte"] == "curadoria-internacional" and f["status"] == "erro" for f in corpo["fontes"]
    )


async def test_coleta_diaria_publica_a_curadoria_internacional_sem_mexer_nos_brasileiros(
    tmp_path, monkeypatch
):
    import httpx

    from worker import scraper_worker
    from worker.http import ClienteHttp

    original = ClienteHttp.__init__

    def init(self, *a, **kw):
        original(
            self,
            *a,
            **{
                **kw,
                "transporte": httpx.MockTransport(lambda r: httpx.Response(200, json=[])),
                "pausa_base": 0,
            },
        )

    async def sem_v4(*a, **kw):
        from worker.fontes_v4 import ResultadoV4

        return ResultadoV4()

    # prazo em 2099: o teste não pode depender de a chamada real ainda estar aberta
    longe = carregar_json(DADOS)["oportunidades"][0] | {"prazo_original": "2099-12-31"}
    arquivo = tmp_path / "curadoria.json"
    arquivo.write_text(json.dumps({"oportunidades": [longe]}), encoding="utf-8")
    monkeypatch.setattr(ClienteHttp, "__init__", init)
    monkeypatch.setattr(scraper_worker, "rodar_fontes_v4", sem_v4)
    monkeypatch.setattr(scraper_worker, "CURADAS_INTERNACIONAL", arquivo)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    snapshot = tmp_path / "s.json"
    assert await scraper_worker.executar(dry_run=True, snapshot=snapshot) == 0
    editais = json.loads(snapshot.read_text(encoding="utf-8"))["editais"]
    inter = [e for e in editais if e["ambito"] == "internacional"]
    assert [e["chave"] for e in inter] == ["curadoria-internacional:ibermuseos-fondo-7-2026"]
    assert inter[0]["via"] == "curadoria do portal" and inter[0]["abrangencia"] is None
    brasil = [e for e in editais if e["ambito"] == "brasil"]
    assert brasil and all(e["elegibilidade_br"] == "verificar" for e in brasil)
