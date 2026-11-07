"""Regex de valores em R$. Cada trecho abaixo foi copiado de um edital real das fixtures
(a primeira asserção confere que o trecho existe lá); a expectativa foi conferida à mão."""

from decimal import Decimal

import pytest

from worker.padronizacao import (
    extrair_valores,
    parse_valor,
    resolver_valores,
    trecho_comprova_valor,
)

D = Decimal

CASOS = [
    ("com o valor total de R$ 4.714,08 (quatro mil e setecentos", [(D("4714.08"), "total")]),
    (
        "receberem apoio financeiro total de R$ 69.000,00 (sessenta e nove mil reais)",
        [(D("69000"), "total")],
    ),
    (
        "O valor total deste edital é de R$ 54.000,00 (cinquenta e quatro mil reais)",
        [(D("54000"), "total")],
    ),
    ("04 bolsas de estudo (R$ 500,00 cada)", [(D("500"), "teto")]),
    (
        "Os vencedores receberão R$ 50 mil e os finalistas, R$ 15 mil.",
        [(D("50000"), "outro"), (D("15000"), "outro")],
    ),
    (
        "a) Destinos nacionais: R$ 7.000,00 b) Destinos no exterior: R$ 17.000,00",
        [(D("7000"), "outro"), (D("17000"), "outro")],
    ),
    ("no valor de R$ 3.000,00 (três mil reais) cada", [(D("3000"), "teto")]),
    (
        "Será destinado o valor de R$ 7.000,00 (sete mil reais) para contemplação de até 1 (um) projeto",
        [(D("7000"), "total")],
    ),
    (
        "com apoio financeiro de R$ 5.000,00 por projeto, totalizando R$ 240.000,00, mediante",
        [(D("5000"), "teto"), (D("240000"), "total")],
    ),
    (
        "R$ 30.000,00 Total do Edital: R$ 3.000.000,00 Total de Vagas: 100",
        [(D("30000"), "outro"), (D("3000000"), "total")],
    ),
    (
        "Com fomento de R$ 3.500,00 por projeto (total R$ 10,5 mil)",
        [(D("3500"), "teto"), (D("10500"), "total")],
    ),
    (
        "Prêmio de R$ 10.000,00 Categoria Coletivo - Prêmio de R$ 16.000,00 6 Prêmios, totalizando R$ 102.000,00.",
        [(D("10000"), "teto"), (D("16000"), "teto"), (D("102000"), "total")],
    ),
    (
        "O investimento total é de R$ 68.000,00, sendo R$ 7.500,00 para cada projeto vencedor da faixa de profissionais e R$ 2.000,00 para cada projeto vencedor",
        [(D("68000"), "total"), (D("7500"), "teto"), (D("2000"), "teto")],
    ),
    ("Premiação: R$ 9.280,00 cada.", [(D("9280"), "teto")]),
    ("incentivo financeiro de até R$ 70.000,00 para implementação", [(D("70000"), "teto")]),
    (
        "20 PROPOSTAS (R$20.000,00 CADA); - FAIXA GRUPOS E COLETIVOS: 20 PROPOSTAS (R$60.000,00 CADA)",
        [(D("20000"), "teto"), (D("60000"), "teto")],
    ),
    (
        "serão destinados três prêmios de R$ 40 mil reais. A segunda categoria selecionará sete projetos, que receberão R$ 30 mil cada.",
        [(D("40000"), "teto"), (D("30000"), "teto")],
    ),
    (
        "totalizando o montante global de R$ 34.000.000,00 (trinta e quatro milhões de reais)",
        [(D("34000000"), "total")],
    ),
    ("serão destinados ao todo R$ 15.000.000,00 (quinze milhões)", [(D("15000000"), "total")]),
    (
        "tem o total de R$ 2.700.000,00 (dois milhões e setecentos mil reais), para a seleção de 30 (trinta) projetos, no valor de R$ 90.000,00 (noventa mil reais) por projeto",
        [(D("2700000"), "total"), (D("90000"), "teto")],
    ),
    ("Total: 9 prêmios - R$ 88.000,00", [(D("88000"), "total")]),
    ("Total: R$96.000,00", [(D("96000"), "total")]),
    ("serão destinados R$ 51.000,00 em financiamento para as quadrilhas", [(D("51000"), "total")]),
    ("Serão destinados R$ 5 milhões à iniciativa", [(D("5000000"), "total")]),
    (
        "até R$ 500 mil, e projetos de atuação mais localizada, até R$ 250 mil",
        [(D("500000"), "teto"), (D("250000"), "teto")],
    ),
    # Garantia por localidade e piso do programa não são teto por projeto (texto real do MinC).
    (
        "Cada proponente pode solicitar o valor máximo de R$ 200.000,00 (duzentos mil reais) por proposta. O programa garante a seleção de, no mínimo, 50 propostas culturais e assegura o montante mínimo de R$ 1.000.000,00 (um milhão de reais) para cada uma das localidades listadas.",
        [(D("200000"), "teto"), (D("1000000"), "outro")],
    ),
    # Dinheiro que não é fomento: taxa cobrada e remuneração de parecerista.
    (
        "será cobrada uma taxa de cofinanciamento no valor de R$ 50,00 por obra submetida",
        [(D("50"), "outro")],
    ),
    (
        "pareceristas credenciados, quando contratados, receberão o valor de R$ 50,00 (cinquenta reais) para cada projeto, não excedendo o limite de R$ 3.050,00",
        [(D("50"), "outro"), (D("3050"), "outro")],
    ),
    # Número digitado com espaço no meio: ignorar é melhor que ler R$ 14.
    ("Valor total: R$ 14. 440,00. 📅 Prazo de inscrição", []),
]


@pytest.mark.parametrize(("trecho", "esperado"), CASOS)
def test_valores_reais(trecho, esperado, textos_reais):
    assert any(trecho in t for t in textos_reais), "trecho não existe nas fixtures"
    achados = [(a.valor, a.papel) for a in extrair_valores(trecho)]
    assert achados == esperado


def test_ao_menos_quinze_casos_reais():
    assert len(CASOS) >= 15


@pytest.mark.parametrize(
    ("numero", "mult", "esperado"),
    [
        ("50.000,00", None, D("50000.00")),
        ("50", "mil", D("50000.00")),
        ("1,2", "milhão", D("1200000.00")),
        ("5", "milhões", D("5000000.00")),
        ("2", "milhoes", D("2000000.00")),
        ("1.250", None, D("1250.00")),
        ("49,90", None, D("49.90")),
    ],
)
def test_parse_valor(numero, mult, esperado):
    assert parse_valor(numero, mult) == esperado


def test_resolver_total_e_teto():
    r = resolver_valores("apoio de R$ 5.000,00 por projeto, totalizando R$ 240.000,00")
    assert r["teto_por_projeto"][0] == D("5000.00")
    assert r["valor_total"][0] == D("240000.00")
    assert "R$ 240.000,00" in r["valor_total"][1]


def test_total_menor_que_teto_e_incoerente():
    r = resolver_valores(
        "Valor total R$ 1.000,00. Cada projeto receberá até R$ 5.000,00 por projeto"
    )
    assert "valor_total" not in r and r["teto_por_projeto"][0] == D("5000.00")


def test_varios_tetos_pega_o_maior():
    r = resolver_valores("até R$ 500 mil, e projetos de atuação mais localizada, até R$ 250 mil")
    assert r["teto_por_projeto"][0] == D("500000.00")


def test_zero_e_ignorado():
    assert extrair_valores("taxa de R$ 0,00") == []


def test_texto_sem_valor():
    assert resolver_valores("Edital sem valores informados no resumo.") == {}


def test_trecho_comprova_valor():
    assert trecho_comprova_valor(D("50000"), "até R$ 50 mil por projeto")
    assert trecho_comprova_valor(D("50000"), "no valor de 50.000,00 reais")
    assert not trecho_comprova_valor(D("60000"), "até R$ 50 mil por projeto")
    assert not trecho_comprova_valor(D("100"), "sem números relevantes aqui")
