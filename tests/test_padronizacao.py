from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

from conftest import carregar

from worker.coletores.mapas_culturais import Instancia, Parametros, converter
from worker.padronizacao import (
    extrair_marco_legal,
    extrair_proponentes,
    janela_em_meses,
    meses_ate_janela,
    padronizar,
    resumo_sem_llm,
    truncar_palavra,
)

AGORA = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def edital_real(arquivo: str, id_: int):
    inst = Instancia(
        slug="pe",
        nome="PE",
        base_url="https://mapacultural.pe.gov.br",
        abrangencia="estadual",
        uf="PE",
    )
    item = next(i for i in carregar(arquivo) if i["id"] == id_)
    bruto, texto = converter(item, inst, Parametros(), AGORA, Counter())
    return padronizar(bruto, AGORA, texto)


def test_edital_real_pernambuco_ponta_a_ponta():
    e = edital_real("mapas_mapacultural.pe.gov.br.json", 3447)
    assert e.valor_total == Decimal("240000.00") and e.teto_por_projeto == Decimal("5000.00")
    assert e.marco_legal == "PNAB"
    assert set(e.proponentes) == {"PF", "MEI", "PJ_COM_FINS", "PJ_SEM_FINS", "COLETIVO"}
    assert e.uf == "PE" and e.abrangencia == "estadual"
    assert e.proveniencia["valor_total"].via == "regex"
    assert "R$ 240.000,00" in e.proveniencia["valor_total"].trecho
    assert e.proveniencia["uf"].via == "api"
    assert e.hash_conteudo and len(e.hash_conteudo) == 64
    assert len(e.resumo_simples) <= 280


def test_edital_sem_valor_no_resumo_fica_nulo():
    e = edital_real("mapas_mapacultural.pe.gov.br.json", 3410)
    assert e.valor_total is None and e.teto_por_projeto is None
    assert "valor_total" not in e.proveniencia


def test_campos_nao_extraidos_continuam_nulos():
    e = edital_real("mapas_mapacultural.pe.gov.br.json", 3447)
    assert e.exige_contrapartida is None and e.documentos_exigidos is None
    assert e.cronograma is None and e.num_cotas is None


def test_hash_e_estavel_e_muda_com_o_conteudo():
    a = edital_real("mapas_mapacultural.pe.gov.br.json", 3447)
    b = edital_real("mapas_mapacultural.pe.gov.br.json", 3447)
    assert a.hash_conteudo == b.hash_conteudo
    c = a.model_copy(update={"titulo": "Outro título"})
    assert c.calcular_hash() != a.hash_conteudo
    d = a.model_copy(update={"coletado_em": datetime(2030, 1, 1, tzinfo=UTC)})
    assert d.calcular_hash() == a.hash_conteudo


def test_proponentes_com_pessoa_juridica_generica_inclui_os_dois_tipos():
    r = extrair_proponentes("Podem participar pessoas jurídicas de direito privado.")
    assert set(r) == {"PJ_COM_FINS", "PJ_SEM_FINS"}


def test_proponentes_qualificados():
    assert set(extrair_proponentes("pessoa jurídica sem fins lucrativos")) == {"PJ_SEM_FINS"}
    assert set(extrair_proponentes("pessoa jurídica com fins lucrativos")) == {"PJ_COM_FINS"}
    assert set(extrair_proponentes("OSC e MEI")) == {"PJ_SEM_FINS", "MEI"}
    assert set(extrair_proponentes("microempreendedor individual")) == {"MEI"}
    assert set(extrair_proponentes("coletivos sem CNPJ e pessoas físicas")) == {"COLETIVO", "PF"}


def test_proponentes_negados_sao_ignorados():
    assert extrair_proponentes("É vedada a participação de pessoas jurídicas.") == {}
    assert extrair_proponentes("Não poderão participar pessoas físicas.") == {}


def test_proponentes_guardam_trecho_literal():
    texto = "Poderão participar pessoas físicas maiores de 18 anos."
    trecho = extrair_proponentes(texto)["PF"]
    assert "pessoas físicas" in trecho and trecho in texto


def test_marco_legal():
    assert extrair_marco_legal("recursos da Lei Paulo Gustavo")[0] == "LPG"
    assert extrair_marco_legal("Política Nacional Aldir Blanc (PNAB)")[0] == "PNAB"
    assert extrair_marco_legal("Lei Aldir Blanc 2")[0] == "Aldir Blanc"
    assert extrair_marco_legal("Lei Rouanet")[0] == "Rouanet"
    assert extrair_marco_legal("edital municipal") is None


def test_resumo_sem_llm_nao_corta_palavra():
    texto = "Palavra " * 80
    r = resumo_sem_llm(texto)
    assert len(r) <= 280 and r.endswith("…") and not r.endswith("Palav…")


def test_resumo_primeira_frase_e_limpa_html():
    r = resumo_sem_llm(
        "<p>Apoio a <b>projetos</b> culturais de todo o estado, com foco em música e dança regional. Segunda frase longa.</p>"
    )
    assert r == "Apoio a projetos culturais de todo o estado, com foco em música e dança regional."


def test_resumo_vazio():
    assert resumo_sem_llm(None) is None and resumo_sem_llm("   ") is None


def test_truncar_palavra_curto_nao_muda():
    assert truncar_palavra("curto", 280) == "curto"


def test_janela_em_meses():
    assert janela_em_meses("fim de janeiro a março") == (1, 3)
    assert janela_em_meses("junho a agosto") == (6, 8)
    assert janela_em_meses("agosto a setembro, em anos de edição") == (8, 9)
    assert janela_em_meses("sem mês") is None and janela_em_meses(None) is None


def test_meses_ate_janela():
    assert meses_ate_janela(1, 3, 11) == 2  # novembro -> janeiro
    assert meses_ate_janela(6, 8, 7) == 0  # dentro da janela
    assert meses_ate_janela(8, 9, 11) == 9
    assert meses_ate_janela(11, 2, 1) == 0  # janela que vira o ano
    assert meses_ate_janela(None, None, 5) is None
