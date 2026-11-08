"""Specs das seções do assistente: schema, coerência entre seções e cópias sincronizadas."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

RAIZ = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((RAIZ / "specs" / "secao.schema.json").read_text(encoding="utf-8"))
CHECAGENS = json.loads((RAIZ / "specs" / "checagens.json").read_text(encoding="utf-8"))
ARQUIVOS = sorted((RAIZ / "specs" / "secoes").glob("*.json"))
SPECS = {a.stem: json.loads(a.read_text(encoding="utf-8")) for a in ARQUIVOS}
node = pytest.mark.skipif(shutil.which("node") is None, reason="Node indisponível")


def test_json_schema_e_valido():
    Draft7Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("nome", list(SPECS))
def test_spec_valida_no_schema(nome):
    erros = sorted(Draft7Validator(SCHEMA).iter_errors(SPECS[nome]), key=lambda e: list(e.path))
    assert not erros, "; ".join(f"{list(e.path)}: {e.message}" for e in erros)
    assert nome.startswith(f"{SPECS[nome]['ordem']:02d}-{SPECS[nome]['id']}"), (
        "arquivo = NN-id.json"
    )


def test_schema_rejeita_specs_quebradas():
    v = Draft7Validator(SCHEMA)
    base = SPECS["01-apresentacao"]
    assert list(v.iter_errors({**base, "id": "Id Inválido"}))
    assert list(v.iter_errors({**base, "perguntas": []}))
    assert list(v.iter_errors({**base, "fluxos": ["outro"]}))
    assert list(
        v.iter_errors(
            {**base, "condicionais": [{"se": "eval(1)", "pergunta": base["perguntas"][0]}]}
        )
    )
    assert list(
        v.iter_errors(
            {
                "id": "equipe",
                "titulo": "Equipe",
                "ordem": 9,
                "ativo": False,
                "perguntas": base["perguntas"],
            }
        )
    )
    sem_instrucao = {k: x for k, x in base.items() if k != "instrucoes_redacao"}
    assert list(v.iter_errors(sem_instrucao))


def test_ids_e_ordens_unicos_e_dependencias_para_tras():
    ids = [s["id"] for s in SPECS.values()]
    ordens = [s["ordem"] for s in SPECS.values()]
    assert len(set(ids)) == len(ids) and sorted(ordens) == list(range(len(ordens)))
    por_id = {s["id"]: s for s in SPECS.values()}
    for s in SPECS.values():
        for dep in s.get("depende_de", []):
            assert dep in por_id, f"{s['id']} depende de {dep}, que não existe"
            assert por_id[dep]["ordem"] < s["ordem"], f"{s['id']} depende de uma seção posterior"


def test_as_12_secoes_estao_ativas_com_perguntas():
    assert [s["id"] for s in SPECS.values() if s.get("ativo", True)] == [
        "ficha", "apresentacao", "justificativa", "objetivos", "metas", "publico",
        "acessibilidade", "democratizacao", "comunicacao", "equipe", "cronograma", "orcamento",
    ]  # fmt: skip
    for s in SPECS.values():
        assert s["perguntas"] and s["fluxos"] and s["instrucoes_redacao"], s["id"]


def test_secoes_estruturadas_apontam_para_a_tabela_certa():
    esperado = {
        "acessibilidade": ("orcamento_itens", "orcamento", "acessibilidade"),
        "comunicacao": ("midia_itens", "midia", None),
        "equipe": ("equipe_membros", "equipe", None),
        "cronograma": ("cronograma_itens", "cronograma", None),
        "orcamento": ("orcamento_itens", "orcamento", None),
    }
    por_id = {s["id"]: s for s in SPECS.values()}
    for id_, (tabela, editor, etapa) in esperado.items():
        saida = por_id[id_]["saida_estruturada"]
        assert (saida["tabela"], saida["editor"], saida.get("etapa_padrao")) == (
            tabela,
            editor,
            etapa,
        )
    # as demais seções são só texto
    assert {i for i, s in por_id.items() if "saida_estruturada" in s} == set(esperado)


def test_condicoes_dos_specs_usam_so_variaveis_conhecidas():
    """`ficha.*` e `criterios.*` (do edital) são o contexto que a função monta para as condicionais."""
    conhecidas = {
        "criterios.exige_acessibilidade", "criterios.contrapartida_exigida",
        "criterios.prazo_execucao_meses", "criterios.teto_orcamento",
    }  # fmt: skip
    usadas = set()
    for s in SPECS.values():
        for c in s.get("condicionais", []):
            usadas |= set(re.findall(r"\b(?:ficha|criterios)\.[A-Za-z_]+", c["se"]))
    assert {u for u in usadas if u.startswith("criterios.")} <= conhecidas
    assert {u for u in usadas if u.startswith("criterios.")}, (
        "as seções 6 a 11 usam os critérios do edital"
    )


def test_nenhuma_pergunta_do_roteiro_foi_perdida():
    contagem = {
        "ficha": (7, 1),
        "apresentacao": (4, 0),
        "justificativa": (4, 1),
        "objetivos": (2, 0),
        "metas": (2, 0),
        "publico": (3, 0),
        # Fase 2: as perguntas do roteiro do prompt (acessibilidade 3+1 e mais a de orçamento)
        "acessibilidade": (4, 1),
        "democratizacao": (2, 2),
        "comunicacao": (4, 0),
        "equipe": (3, 0),
        "cronograma": (3, 1),
        "orcamento": (5, 1),
    }
    for s in SPECS.values():
        if s["id"] in contagem:
            assert (len(s["perguntas"]), len(s["condicionais"])) == contagem[s["id"]], s["id"]


def test_checagens_referenciadas_existem_no_catalogo():
    for s in SPECS.values():
        for c in s.get("checagens", []):
            assert c in CHECAGENS, f"{s['id']}: checagem desconhecida {c}"


def test_perguntas_sensiveis_nao_passam_pelo_chat():
    ficha = SPECS["00-ficha"]
    por_id = {
        p["id"]: p for p in ficha["perguntas"] + [c["pergunta"] for c in ficha["condicionais"]]
    }
    assert por_id["cotas"]["canal"] == "formulario" and por_id["edital"]["canal"] == "formulario"
    for s in SPECS.values():
        for p in s.get("perguntas", []):
            assert not re.search(
                r"\b(cpf|cnpj|rg|endere[çc]o|telefone|e-mail)\b", p["texto"], re.I
            ), p["id"]


@node
def test_logica_compartilhada_em_node():
    r = subprocess.run(
        ["node", "--test", str(RAIZ / "specs" / "lib")],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-400:]


@node
def test_copias_do_front_e_da_funcao_estao_em_sincronia():
    r = subprocess.run(
        ["node", str(RAIZ / "scripts" / "sincronizar_specs.mjs"), "--check"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
