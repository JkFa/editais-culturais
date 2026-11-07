import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from worker.enriquecimento_llm import (
    CacheLLM,
    ClienteGemini,
    ResultadoValidado,
    aplicar,
    chave_cache,
    enriquecer_editais,
    validar_resposta,
)
from worker.modelos import Edital

TEXTO = (
    "Edital Teste A. Serão destinados R$ 100.000,00 ao edital, com teto de R$ 20 mil por projeto. "
    "Podem participar pessoas físicas e MEI. Não há contrapartida obrigatória."
)
AGORA = datetime(2026, 11, 6, tzinfo=UTC)


def base() -> Edital:
    e = Edital(
        fonte="mapas:teste",
        id_externo="1",
        url_origem="https://x.invalid/1",
        titulo="Edital Teste A",
        orgao_fomento="Fictício",
        linguagens=["Música"],
        inscricao_fim=AGORA + timedelta(days=3),
    )
    return e


def test_valor_com_trecho_que_contem_o_numero_e_aceito():
    r = validar_resposta(
        {
            "valor_total": {"valor": 100000, "trecho": "Serão destinados R$ 100.000,00 ao edital"},
            "teto_por_projeto": {"valor": 20000, "trecho": "teto de R$ 20 mil por projeto"},
        },
        TEXTO,
    )
    assert r.valor_total[0] == Decimal("100000.00") and r.teto_por_projeto[0] == Decimal("20000.00")


def test_valor_cujo_trecho_nao_contem_o_numero_e_descartado():
    r = validar_resposta(
        {"valor_total": {"valor": 250000, "trecho": "Serão destinados R$ 100.000,00 ao edital"}},
        TEXTO,
    )
    assert r.valor_total is None


def test_trecho_inventado_que_nao_esta_no_texto_e_descartado():
    r = validar_resposta(
        {"valor_total": {"valor": 500000, "trecho": "o valor total é de R$ 500.000,00"}}, TEXTO
    )
    assert r.valor_total is None


def test_proponente_exige_trecho_literal_e_codigo_valido():
    r = validar_resposta(
        {
            "proponentes": [
                {"codigo": "PF", "trecho": "pessoas físicas e MEI"},
                {"codigo": "PJ_COM_FINS", "trecho": "empresas privadas"},  # trecho não existe
                {"codigo": "ALIEN", "trecho": "pessoas físicas e MEI"},  # código inválido
            ]
        },
        TEXTO,
    )
    assert list(r.proponentes) == ["PF"]


def test_contrapartida_com_evidencia():
    r = validar_resposta(
        {"exige_contrapartida": {"valor": False, "trecho": "Não há contrapartida obrigatória"}},
        TEXTO,
    )
    assert r.exige_contrapartida == (False, "Não há contrapartida obrigatória")
    r2 = validar_resposta(
        {"exige_contrapartida": {"valor": True, "trecho": "contrapartida de 10%"}}, TEXTO
    )
    assert r2.exige_contrapartida is None


def test_resposta_fora_do_esquema_vira_vazia():
    assert validar_resposta({"valor_total": "muito"}, TEXTO) == ResultadoValidado()


def test_total_menor_que_teto_descarta_total():
    r = validar_resposta(
        {
            "valor_total": {"valor": 100000, "trecho": "Serão destinados R$ 100.000,00 ao edital"},
            "teto_por_projeto": {"valor": 20000, "trecho": "teto de R$ 20 mil por projeto"},
        },
        TEXTO.replace("100.000,00", "100,00"),
    )
    assert r.valor_total is None


def test_aplicar_nao_sobrescreve_regex_e_registra_proveniencia():
    ed = base().model_copy(update={"valor_total": Decimal("1.00")})
    r = validar_resposta(
        {
            "valor_total": {"valor": 100000, "trecho": "Serão destinados R$ 100.000,00 ao edital"},
            "teto_por_projeto": {"valor": 20000, "trecho": "teto de R$ 20 mil por projeto"},
            "proponentes": [{"codigo": "MEI", "trecho": "pessoas físicas e MEI"}],
            "resumo_simples": "Apoio a projetos musicais.",
        },
        TEXTO,
    )
    novo = aplicar(ed, r)
    assert novo.valor_total == Decimal("1.00")  # regex venceu
    assert novo.teto_por_projeto == Decimal("20000.00")
    assert novo.proveniencia["teto_por_projeto"].via == "llm"
    assert "20 mil" in novo.proveniencia["teto_por_projeto"].trecho
    assert novo.proponentes == ["MEI"] and novo.resumo_simples == "Apoio a projetos musicais."
    assert novo.hash_conteudo != ed.hash_conteudo


def gemini(resposta: dict | None, chamadas: list):
    def tratar(req: httpx.Request) -> httpx.Response:
        chamadas.append(req)
        if resposta is None:
            return httpx.Response(500)
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(resposta)}]}}]}
        )

    return ClienteGemini("chave-teste", intervalo_s=0, transporte=httpx.MockTransport(tratar))


async def test_enriquecer_usa_cache_na_segunda_vez(tmp_path):
    chamadas: list = []
    cache = CacheLLM(tmp_path / "c.json")
    resp = {"teto_por_projeto": {"valor": 20000, "trecho": "teto de R$ 20 mil por projeto"}}
    cl = gemini(resp, chamadas)
    ed = base()
    out = await enriquecer_editais([ed], {ed.chave: TEXTO}, cl, cache, 10)
    assert out[0].teto_por_projeto == Decimal("20000.00") and len(chamadas) == 1
    cache.salvar()
    cache2 = CacheLLM(tmp_path / "c.json")
    out2 = await enriquecer_editais([ed], {ed.chave: TEXTO}, cl, cache2, 10)
    assert out2[0].teto_por_projeto == Decimal("20000.00") and len(chamadas) == 1
    assert chamadas[0].headers["x-goog-api-key"] == "chave-teste"
    await cl.fechar()


async def test_erro_do_llm_segue_com_o_resultado_do_regex(tmp_path):
    chamadas: list = []
    cl = gemini(None, chamadas)
    ed = base()
    out = await enriquecer_editais([ed], {ed.chave: TEXTO}, cl, CacheLLM(tmp_path / "c.json"), 10)
    assert out == [ed]
    await cl.fechar()


async def test_limite_de_chamadas_por_execucao(tmp_path):
    chamadas: list = []
    cl = gemini({}, chamadas)
    eds = [base().model_copy(update={"id_externo": str(i)}) for i in range(5)]
    textos = {e.chave: f"{TEXTO} {e.id_externo}" for e in eds}
    await enriquecer_editais(eds, textos, cl, CacheLLM(tmp_path / "c.json"), 2)
    assert len(chamadas) == 2
    await cl.fechar()


def test_cache_descarta_entradas_nao_usadas(tmp_path):
    c = CacheLLM(tmp_path / "c.json")
    c.guardar("a", ResultadoValidado())
    c.guardar("b", ResultadoValidado())
    c.salvar()
    c2 = CacheLLM(tmp_path / "c.json")
    c2.obter("a")
    c2.salvar()
    assert list(json.loads((tmp_path / "c.json").read_text())) == ["a"]


def test_chave_de_cache_depende_do_texto_e_do_modelo():
    assert chave_cache("a", "m1") != chave_cache("b", "m1") != chave_cache("a", "m2")
