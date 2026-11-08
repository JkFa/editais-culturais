from datetime import date
from pathlib import Path

import pytest

from worker.coletores.curadoria import (
    ErroCuradoria,
    avisos_de_vencimento,
    carregar_curadoria,
)

CURADAS = Path(__file__).parent.parent / "worker" / "fontes_curadas.yaml"

BASE = """
editais:
  - id_externo: edital-teste-a
    titulo: "Edital Teste A"
    orgao_fomento: "Órgão Fictício"
    url_origem: "https://exemplo.invalid/a"
    resumo_simples: "Resumo fictício."
    linguagens: ["Música"]
    abrangencia: nacional
    proponentes: ["PF"]
    inscricao_fim: "2027-01-31"
    verificado_em: "2026-11-06"
    evidencias:
      inscricao_fim: "até 31/01/2027"
      proponentes: "pessoas físicas"
"""


def escrever(tmp_path, texto):
    caminho = tmp_path / "curadas.yaml"
    caminho.write_text(texto, encoding="utf-8")
    return caminho


def test_arquivo_real_valida_e_conta_entradas():
    r = carregar_curadoria(CURADAS, date(2026, 11, 6))
    assert len(r.editais) == 5 and len(r.radar) == 8
    assert r.avisos == []
    for e in r.editais:
        assert e.fonte == "curadoria" and e.curado
        assert e.proveniencia["inscricao_fim"].via == "curadoria"
        assert e.proveniencia["inscricao_fim"].trecho


def test_cessao_de_espaco_nao_tem_valores():
    r = carregar_curadoria(CURADAS, date(2026, 11, 6))
    assert all(e.tipo_apoio == "cessao_de_espaco" for e in r.editais)
    assert all(e.valor_total is None and e.teto_por_projeto is None for e in r.editais)


def test_fim_com_horario_da_curadoria():
    r = {e.id_externo: e for e in carregar_curadoria(CURADAS, date(2026, 11, 6)).editais}
    cta = r["funarte-aberta-2026-cta"]
    assert cta.fim_tem_horario is True
    assert cta.inscricao_fim.isoformat() == "2027-04-30T20:59:00+00:00"
    mg = r["funarte-aberta-2026-complexo-mg"]
    assert mg.fim_tem_horario is False


def test_radar_ordena_por_janela():
    radar = {x.id_externo: x for x in carregar_curadoria(CURADAS, date(2026, 11, 6)).radar}
    assert (radar["sesc-rj-pulsar"].janela_mes_inicio, radar["sesc-rj-pulsar"].janela_mes_fim) == (
        1,
        3,
    )
    assert radar["sesc-pr-artes-da-cena"].janela_mes_inicio == 6


def test_aviso_de_verificacao_vencida():
    hoje = date(2026, 12, 21)  # 45 dias depois de 06/11 ainda não vence
    assert carregar_curadoria(CURADAS, hoje).avisos == []
    avisos = carregar_curadoria(CURADAS, date(2026, 12, 22)).avisos
    assert len(avisos) == 10 and "46 dias" in avisos[0]


def test_aviso_isolado():
    assert avisos_de_vencimento([("x", "2026-01-01")], date(2026, 3, 1))[0].startswith(
        "curadoria: 'x'"
    )
    assert avisos_de_vencimento([("x", "2026-01-01")], date(2026, 1, 10)) == []


def test_minimo_valido(tmp_path):
    r = carregar_curadoria(escrever(tmp_path, BASE), date(2026, 11, 6))
    assert r.editais[0].proponentes == ["PF"]


def test_valor_sem_evidencia_e_rejeitado(tmp_path):
    texto = BASE + "    valor_total: 1000\n"
    with pytest.raises(ErroCuradoria, match="valor_total preenchido sem trecho"):
        carregar_curadoria(escrever(tmp_path, texto), date(2026, 11, 6))


def test_evidencia_sem_o_numero_e_rejeitada(tmp_path):
    texto = BASE.replace(
        'proponentes: "pessoas físicas"',
        'proponentes: "pessoas físicas"\n      valor_total: "valor a definir"',
    )
    texto += "    valor_total: 1000\n"
    with pytest.raises(ErroCuradoria, match="não contém o valor"):
        carregar_curadoria(escrever(tmp_path, texto), date(2026, 11, 6))


def test_valor_com_evidencia_correta(tmp_path):
    texto = BASE.replace(
        'proponentes: "pessoas físicas"',
        'proponentes: "pessoas físicas"\n      valor_total: "total de R$ 1.000,00"',
    )
    texto += "    valor_total: 1000\n"
    r = carregar_curadoria(escrever(tmp_path, texto), date(2026, 11, 6))
    assert float(r.editais[0].valor_total) == 1000.0


def test_proponentes_sem_evidencia(tmp_path):
    texto = BASE.replace('      proponentes: "pessoas físicas"\n', "")
    with pytest.raises(ErroCuradoria, match="proponentes preenchido sem trecho"):
        carregar_curadoria(escrever(tmp_path, texto), date(2026, 11, 6))


def test_cessao_com_valor_e_rejeitada(tmp_path):
    texto = BASE.replace(
        'proponentes: "pessoas físicas"',
        'proponentes: "pessoas físicas"\n      valor_total: "R$ 5,00"',
    )
    texto += "    valor_total: 5\n    tipo_apoio: cessao_de_espaco\n"
    with pytest.raises(ErroCuradoria, match="cessão"):
        carregar_curadoria(escrever(tmp_path, texto), date(2026, 11, 6))


def test_linguagem_fora_da_taxonomia(tmp_path):
    with pytest.raises(ErroCuradoria, match="linguagens inválidas"):
        carregar_curadoria(
            escrever(tmp_path, BASE.replace('"Música"', '"Esportes"')), date(2026, 11, 6)
        )


def test_proponente_invalido(tmp_path):
    with pytest.raises(ErroCuradoria):
        carregar_curadoria(
            escrever(tmp_path, BASE.replace('["PF"]', '["ALIEN"]')), date(2026, 11, 6)
        )


def test_id_duplicado(tmp_path):
    bloco = BASE.split("editais:\n")[1]
    with pytest.raises(ErroCuradoria, match="duplicado"):
        carregar_curadoria(escrever(tmp_path, BASE + bloco), date(2026, 11, 6))


def test_verificado_em_obrigatorio(tmp_path):
    with pytest.raises(ErroCuradoria):
        carregar_curadoria(
            escrever(tmp_path, BASE.replace('    verificado_em: "2026-11-06"\n', "")),
            date(2026, 11, 6),
        )


def test_resumo_acima_de_280(tmp_path):
    longo = "x" * 281
    with pytest.raises(ErroCuradoria):
        carregar_curadoria(
            escrever(tmp_path, BASE.replace("Resumo fictício.", longo)), date(2026, 11, 6)
        )
