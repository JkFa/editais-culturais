"""Migrations v4 num PostgreSQL real (Docker): ordem completa, idempotência, constraint e RLS."""

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from sql_util import PREAMBULO_SUPABASE

MIGRATIONS = sorted(
    (Path(__file__).resolve().parent.parent / "supabase" / "migrations").glob("*.sql")
)
SCHEMA = Path(__file__).resolve().parent.parent / "supabase" / "schema.sql"
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker indisponível")


def psql(nome: str, sql: str, papel: str | None = None, check: bool = True):
    pre = f"set role {papel};\n" if papel else ""
    return subprocess.run(
        ["docker", "exec", "-i", nome, "psql", "-U", "postgres", "-At", "-v", "ON_ERROR_STOP=1"],
        input=pre + sql,
        text=True,
        capture_output=True,
        check=check,
    )


@pytest.fixture(scope="module")
def banco():
    nome = f"pg-v4-{int(time.time())}"
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                nome,
                "-e",
                "POSTGRES_PASSWORD=x",
                "postgres:16",
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"postgres indisponível: {exc.stderr[:80]}")
    for _ in range(60):
        if (
            subprocess.run(
                ["docker", "exec", nome, "pg_isready", "-U", "postgres"], capture_output=True
            ).returncode
            == 0
        ):
            break
        time.sleep(0.5)
    time.sleep(1.5)
    psql(
        nome,
        PREAMBULO_SUPABASE,
    )
    yield nome
    subprocess.run(["docker", "rm", "-f", nome], capture_output=True)


def test_migrations_na_ordem_e_duas_vezes(banco):
    for _ in range(2):
        for m in MIGRATIONS:
            psql(banco, m.read_text(encoding="utf-8"))
    colunas = psql(
        banco,
        "select column_name from information_schema.columns where table_name='editais_abertos'",
    ).stdout.split()
    assert {"via", "fontes_alternativas", "requisitos", "tipo_apoio"} <= set(colunas)


def test_schema_sql_completo_tambem_e_idempotente(banco):
    psql(banco, SCHEMA.read_text(encoding="utf-8"))
    psql(banco, SCHEMA.read_text(encoding="utf-8"))


def test_tipo_apoio_so_aceita_os_cinco_valores(banco):
    base = "insert into editais(fonte,id_externo,url_origem,titulo,orgao_fomento,inscricao_fim,tipo_apoio) values ('t','{i}','u','Edital Teste A','o',now(),{v});"
    for i, v in enumerate(
        [
            "'fomento_em_dinheiro'",
            "'premio'",
            "'patrocinio_incentivado'",
            "'cessao_de_espaco'",
            "'credenciamento'",
            "null",
        ]
    ):
        psql(banco, base.format(i=i, v=v))
    assert psql(banco, base.format(i=99, v="'doacao'"), check=False).returncode != 0


def test_fontes_status_anon_le_mas_nao_escreve(banco):
    psql(
        banco,
        "insert into fontes_status(fonte,nome,tipo,metodo,url,ativa,status) values ('f1','Fonte Teste','oficial','html','https://x.invalid',true,'ok');",
        papel="service_role",
    )
    assert (
        psql(banco, "select count(*) from fontes_status;", papel="anon")
        .stdout.strip()
        .endswith("1")
    )
    assert (
        psql(
            banco,
            "insert into fontes_status(fonte,nome,tipo,metodo,url,ativa,status) values ('f2','x','oficial','html','u',true,'ok');",
            papel="anon",
            check=False,
        ).returncode
        != 0
    )
    assert (
        psql(
            banco,
            "insert into fontes_status(fonte,nome,tipo,metodo,url,ativa,status) values ('f3','x','oficial','html','u',true,'qualquer');",
            papel="service_role",
            check=False,
        ).returncode
        != 0
    )
    assert (
        psql(
            banco,
            "insert into fontes_status(fonte,nome,tipo,metodo,url,ativa,status) values ('f4','x','agregador','html','u',true,'ok');",
            papel="service_role",
            check=False,
        ).returncode
        != 0
    )


def test_payload_do_worker_cabe_nas_colunas(banco):
    import json

    snap = json.loads(
        (Path(__file__).resolve().parent.parent / "web" / "data" / "snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    cols = set(
        psql(
            banco, "select column_name from information_schema.columns where table_name='editais'"
        ).stdout.split()
    )
    chaves = set().union(*[set(e) for e in snap["editais"]]) - {"chave"}
    assert chaves <= cols, chaves - cols


def test_internacional_colunas_padroes_constraints_e_view(banco):
    """Migration 20261109: editais antigos ficam 'brasil'/'verificar'; checks recusam lixo."""
    psql(
        banco,
        "insert into editais(fonte,id_externo,url_origem,titulo,orgao_fomento,inscricao_fim) "
        "values ('t','intl-padrao','u','Edital Padrao','o',now() + interval '10 days');",
    )
    assert (
        psql(
            banco,
            "select ambito || '/' || elegibilidade_br from editais where id_externo='intl-padrao';",
        ).stdout.strip()
        == "brasil/verificar"
    )
    colunas = psql(
        banco,
        "select column_name from information_schema.columns where table_name='editais_abertos'",
    ).stdout.split()
    assert {"ambito", "elegibilidade_br", "custos_cobertos", "titulo_original", "resumo_pt"} <= set(
        colunas
    )
    ok = (
        "insert into editais(fonte,id_externo,url_origem,titulo,orgao_fomento,inscricao_fim,ambito,"
        "pais_destino,elegibilidade_br,tipo_oportunidade,custos_cobertos,idioma_original,moeda,"
        "valor_original,prazo_original,prazo_fuso,titulo_original) values ('t','{i}','u','Chamada','o',"
        "now() + interval '10 days',{extra});"
    )
    bom = (
        "'internacional','DE','aberto','residencia',array['viagem','hospedagem'],'en','EUR',"
        "1500,now(),'Europe/Berlin','Original'"
    )
    psql(banco, ok.format(i="intl-ok", extra=bom))
    for ruim in [
        bom.replace("'internacional'", "'lua'"),
        bom.replace("'aberto'", "'talvez'"),
        bom.replace("'residencia'", "'sorteio'"),
        bom.replace("'hospedagem'", "'iate'"),
        bom.replace("1500", "-1"),
    ]:
        assert psql(banco, ok.format(i="intl-ruim", extra=ruim), check=False).returncode != 0
    assert (
        psql(
            banco,
            "select count(*) from editais_abertos where ambito='internacional';",
            papel="anon",
        )
        .stdout.strip()
        .endswith("1")
    )
