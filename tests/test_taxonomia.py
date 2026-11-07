import pytest

from worker.modelos import LINGUAGENS
from worker.taxonomia import detectar_uf, mapear_area, mapear_linguagens


def test_todas_as_areas_reais_das_fixtures_tem_mapeamento(itens_reais):
    areas = {a for i in itens_reais for a in (i.get("terms") or {}).get("area", [])}
    assert len(areas) > 200
    sem_mapa = sorted(a for a in areas if mapear_area(a) is None)
    assert sem_mapa == []


@pytest.mark.parametrize(
    ("area", "esperado"),
    [
        ("Música", "Música"),
        ("Música Eletrônica", "Música"),
        ("Dança Afro", "Teatro & Dança"),
        ("Frevo (dança)", "Teatro & Dança"),
        ("Frevo", "Música"),
        ("Teatro de Rua", "Teatro & Dança"),
        ("Circo Tradicional", "Teatro & Dança"),
        ("Cinema", "Cinema & Audiovisual"),
        ("Produção Audiovisual", "Cinema & Audiovisual"),
        ("Videoarte", "Artes Visuais"),
        ("Fotografia", "Fotografia"),
        ("Artes Visuais", "Artes Visuais"),
        ("Arte de Plásticas", "Artes Visuais"),
        ("Literatura Infantil", "Literatura"),
        ("Criação literária", "Literatura"),
        ("Patrimônio Cultural Imaterial", "Patrimônio"),
        ("Museu tombado", "Patrimônio"),
        ("Outra", "Multiáreas"),
        ("Cultura e Educação", "Multiáreas"),
        ("Produção Cultural", "Multiáreas"),
    ],
)
def test_mapear_area(area, esperado):
    assert mapear_area(area) == esperado


def test_area_desconhecida_vai_para_multiareas_e_entra_no_log():
    nao_mapeadas: set[str] = set()
    ling, _ = mapear_linguagens(["Xyzzy Inexistente"], "", nao_mapeadas)
    assert ling == ["Multiáreas"]
    assert nao_mapeadas == {"Xyzzy Inexistente"}


def test_multiareas_some_quando_ha_linguagem_especifica():
    ling, _ = mapear_linguagens(["Outra", "Dança", "Música"])
    assert ling == ["Música", "Teatro & Dança"]


def test_linguagens_na_ordem_da_taxonomia():
    ling, _ = mapear_linguagens(["Literatura", "Cinema", "Música"])
    assert ling == [x for x in LINGUAGENS if x in ling]


def test_sem_areas_infere_pelo_titulo():
    ling, trecho = mapear_linguagens([], "EDITAL Nº 016/2026 – MÚSICA - SELEÇÃO DE PROJETOS")
    assert ling == ["Música"] and trecho == "MÚSICA"
    ling, _ = mapear_linguagens([], "EDITAL Nº 013/ 2026 - ARTES CÊNICAS SELEÇÃO")
    assert ling == ["Teatro & Dança"]
    ling, _ = mapear_linguagens([], "Edital 013 - Formação e Qualificação Cultural")
    assert ling == ["Multiáreas"]


def test_titulo_nao_e_usado_quando_ha_areas():
    ling, trecho = mapear_linguagens(["Outra"], "Edital de música")
    assert ling == ["Multiáreas"] and trecho is None


def test_detectar_uf():
    assert detectar_uf("recursos do Estado do Ceará para artistas")[0] == "CE"
    assert detectar_uf("realizado em Petrolândia/PE neste ano")[0] == "PE"
    assert detectar_uf("Natal - RN")[0] == "RN"
    assert detectar_uf("Governo do Estado de São Paulo")[0] == "SP"


def test_uf_ambigua_ou_ausente_retorna_none():
    assert detectar_uf("Estado do Ceará e Estado da Bahia") is None
    assert detectar_uf("edital nacional para todo o Brasil") is None
