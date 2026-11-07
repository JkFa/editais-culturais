import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def carregar(nome: str) -> list[dict]:
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


def todos_os_itens() -> list[dict]:
    itens: list[dict] = []
    for arquivo in sorted(FIXTURES.glob("mapas_*.json")):
        itens += json.loads(arquivo.read_text(encoding="utf-8"))
    return itens


@pytest.fixture(scope="session")
def itens_reais() -> list[dict]:
    return todos_os_itens()


@pytest.fixture(scope="session")
def textos_reais(itens_reais) -> list[str]:
    from worker.padronizacao import limpar_texto

    return [
        limpar_texto(f"{i.get('shortDescription') or ''} {i.get('longDescription') or ''}")
        for i in itens_reais
    ]
