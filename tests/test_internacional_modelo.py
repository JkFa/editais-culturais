"""Modelo de dados internacional (Fase 1): padrões, validações e regressão dos editais brasileiros."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from worker.modelos import CAMPOS_INTERNACIONAIS, Edital

SNAPSHOT = Path(__file__).resolve().parent.parent / "web" / "data" / "snapshot.json"
FIM = datetime(2026, 12, 1, 23, 59, 59, tzinfo=UTC)


def _base(**extra):
    return {
        "fonte": "t",
        "id_externo": "1",
        "url_origem": "https://exemplo.invalid/1",
        "titulo": "Chamada de teste",
        "orgao_fomento": "Org",
        "inscricao_fim": FIM,
    } | extra


def test_edital_brasileiro_tem_os_padroes_da_fase_1():
    e = Edital(**_base(abrangencia="estadual", uf="CE"))
    assert e.ambito == "brasil" and e.elegibilidade_br == "verificar"
    assert e.pais_destino is None and e.custos_cobertos is None and e.valor_brl is None


def test_hash_do_edital_brasileiro_nao_muda_com_os_campos_novos():
    """Sem isso, a primeira coleta regravaria todos os editais e zeraria o 'coletado em'."""
    todas = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["editais"]
    linhas = [x for x in todas if x.get("ambito", "brasil") == "brasil"]
    assert linhas
    for linha in linhas:
        e = Edital.model_validate({k: v for k, v in linha.items() if k != "chave"})
        assert e.ambito == "brasil"
        assert e.calcular_hash() == linha["hash_conteudo"], linha["chave"]


def test_linhas_internacionais_do_snapshot_sao_validas_e_o_hash_confere():
    for linha in json.loads(SNAPSHOT.read_text(encoding="utf-8"))["editais"]:
        if linha.get("ambito", "brasil") == "brasil":
            continue
        e = Edital.model_validate({k: v for k, v in linha.items() if k != "chave"})
        assert e.abrangencia is None and e.calcular_hash() == linha["hash_conteudo"], linha["chave"]


def test_hash_muda_quando_ha_dado_internacional():
    base = Edital(**_base())
    inter = Edital(**_base(ambito="internacional", pais_destino="DE", idioma_original="en"))
    assert base.calcular_hash() != inter.calcular_hash()


def test_internacional_nao_usa_abrangencia_do_brasil():
    with pytest.raises(ValidationError, match="ambito"):
        Edital(**_base(ambito="internacional", abrangencia="nacional"))


@pytest.mark.parametrize(
    "campo, valor",
    [
        ("custos_cobertos", ["viagem", "yacht"]),
        ("prazo_fuso", "Marte/Olympus"),
        ("valor_original", -1),
        ("elegibilidade_br", "talvez"),
        ("tipo_oportunidade", "sorteio"),
        ("ambito", "lua"),
    ],
)
def test_valores_invalidos_sao_recusados(campo, valor):
    with pytest.raises(ValidationError):
        Edital(**_base(**{campo: valor}))


def test_linha_do_banco_tem_todas_as_colunas_novas_e_numeros_em_float():
    e = Edital(**_base(ambito="internacional", valor_original="1500.50", moeda="EUR"))
    linha = e.para_linha()
    assert set(CAMPOS_INTERNACIONAIS) <= set(linha)
    assert linha["valor_original"] == 1500.5 and linha["valor_brl"] is None
