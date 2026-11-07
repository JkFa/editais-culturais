"""Valida a migration de visitas num PostgreSQL real (Docker). Pulado sem Docker."""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MIGRATION = RAIZ / "supabase" / "migrations" / "20261107120000_visitas.sql"

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker indisponível")


def psql(
    nome: str, sql: str, papel: str | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    prefixo = f"set role {papel};\n" if papel else ""
    return subprocess.run(
        ["docker", "exec", "-i", nome, "psql", "-U", "postgres", "-At", "-v", "ON_ERROR_STOP=1"],
        input=prefixo + sql,
        text=True,
        capture_output=True,
        check=check,
    )


@pytest.fixture(scope="module")
def banco():
    nome = f"pg-visitas-{int(time.time())}"
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
        pytest.skip(f"não foi possível subir o postgres: {exc.stderr[:100]}")
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
        "create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;",
    )
    psql(nome, MIGRATION.read_text(encoding="utf-8"))
    yield nome
    subprocess.run(["docker", "rm", "-f", nome], capture_output=True)


def resumo(nome: str) -> dict:
    return json.loads(
        psql(nome, "select public.visitas_resumo();", papel="anon").stdout.strip().splitlines()[-1]
    )


def registrar(nome: str, n: int, pagina="home", pais="BR", uf="MG", cidade="Ipatinga"):
    for _ in range(n):
        psql(
            nome,
            f"select public.registrar_visita_agregada('{pagina}','{pais}','{uf}','{cidade}');",
            papel="service_role",
        )


def test_migration_e_idempotente(banco):
    psql(banco, MIGRATION.read_text(encoding="utf-8"))


def test_resumo_vazio(banco):
    r = resumo(banco)
    assert (
        r["total_geral"] == 0
        and r["por_uf"] == []
        and r["top_cidades"] == []
        and r["outras_cidades"] == 0
    )


def test_cidade_so_aparece_com_cinco_visitas_ou_mais(banco):
    registrar(banco, 5, cidade="Ipatinga")  # exatamente o mínimo: aparece
    registrar(banco, 4, uf="CE", cidade="Sobral")  # abaixo: vira "outras cidades"
    registrar(banco, 2, uf="CE", cidade="Pacajus")
    registrar(banco, 3, pais="PT", uf="ZZ", cidade="")  # exterior: só país
    registrar(banco, 2, pais="ZZ", uf="ZZ", cidade="")  # localização desconhecida
    r = resumo(banco)
    assert [c["cidade"] for c in r["top_cidades"]] == ["Ipatinga"]
    assert r["top_cidades"][0]["total"] == 5
    assert r["outras_cidades"] == 6  # Sobral 4 + Pacajus 2
    assert r["total_geral"] == 16 and r["total_30d"] == 16
    assert {x["uf"]: x["total"] for x in r["por_uf"]} == {"MG": 5, "CE": 6}
    assert r["estados_30d"] == 2
    nomes = json.dumps(r)
    assert "Sobral" not in nomes and "Pacajus" not in nomes


def test_cidades_se_somam_entre_dias_e_paginas(banco):
    registrar(banco, 3, pagina="editais", uf="CE", cidade="Sobral")  # 4 + 3 = 7 >= 5
    r = resumo(banco)
    assert "Sobral" in [c["cidade"] for c in r["top_cidades"]]
    assert r["outras_cidades"] == 2


def test_top_limitado_a_dez(banco):
    for i in range(12):
        registrar(banco, 5, uf="SP", cidade=f"Cidade Teste {i:02d}")
    assert len(resumo(banco)["top_cidades"]) == 10


def test_visitas_antigas_ficam_so_no_total_geral(banco):
    psql(
        banco,
        "insert into visitas_agregadas values (current_date - 90, 'home', 'BR', 'AM', 'Manaus', 50);",
    )
    r = resumo(banco)
    assert "AM" not in {x["uf"] for x in r["por_uf"]}
    assert all(c["cidade"] != "Manaus" for c in r["top_cidades"])
    assert r["total_geral"] - r["total_30d"] == 50


def test_anon_nao_le_nem_escreve_a_tabela(banco):
    assert (
        psql(banco, "select * from visitas_agregadas;", papel="anon", check=False).returncode != 0
    )
    assert (
        psql(
            banco,
            "insert into visitas_agregadas(dia,pagina) values (current_date,'home');",
            papel="anon",
            check=False,
        ).returncode
        != 0
    )
    assert (
        psql(
            banco,
            "select registrar_visita_agregada('home','BR','MG','x');",
            papel="anon",
            check=False,
        ).returncode
        != 0
    )
    assert (
        psql(
            banco,
            "select registrar_visita_agregada('home','BR','MG','x');",
            papel="authenticated",
            check=False,
        ).returncode
        != 0
    )


def test_pagina_invalida_e_rejeitada(banco):
    assert (
        psql(
            banco,
            "select registrar_visita_agregada('admin','BR','MG','x');",
            papel="service_role",
            check=False,
        ).returncode
        != 0
    )


def test_tabela_nao_tem_coluna_de_ip(banco):
    colunas = psql(
        banco,
        "select column_name from information_schema.columns where table_name='visitas_agregadas';",
    ).stdout.split()
    assert sorted(colunas) == ["cidade", "dia", "pagina", "pais", "total", "uf"]
