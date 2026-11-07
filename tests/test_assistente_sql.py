"""Migração do assistente num PostgreSQL real (Docker): RLS, histórico, limite diário, LGPD."""

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from sql_util import PREAMBULO_SUPABASE

RAIZ = Path(__file__).resolve().parent.parent
MIGRATIONS = sorted((RAIZ / "supabase" / "migrations").glob("*.sql"))
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker indisponível")

ANA, BETO = str(uuid.uuid4()), str(uuid.uuid4())


def psql(nome: str, sql: str, usuario: str | None = None, papel: str = "authenticated", check=True):
    pre = ""
    if usuario:
        pre = f"set role {papel}; set request.jwt.claim.sub = '{usuario}';\n"
    elif papel != "postgres":
        pre = f"set role {papel};\n"
    r = subprocess.run(
        ["docker", "exec", "-i", nome, "psql", "-U", "postgres", "-At", "-v", "ON_ERROR_STOP=1"],
        input=pre + sql,
        text=True,
        capture_output=True,
    )
    if check and r.returncode:
        pytest.fail(f"psql falhou: {r.stderr.strip()}\nSQL: {sql[:200]}")
    return r


def saida(r) -> str:
    """Primeira linha de resultado (ignora as etiquetas de comando: SET, INSERT 0 1...)."""
    linhas = [x for x in r.stdout.splitlines() if x and not x.split()[0].isupper()]
    return linhas[0] if linhas else ""


@pytest.fixture(scope="module")
def banco():
    nome = f"pg-assist-{int(time.time())}"
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
    psql(nome, PREAMBULO_SUPABASE, papel="postgres")
    for _ in range(2):  # idempotência
        for m in MIGRATIONS:
            psql(nome, m.read_text(encoding="utf-8"), papel="postgres")
    psql(
        nome,
        f"insert into auth.users(id,email) values ('{ANA}','ana@exemplo.invalid'),('{BETO}','beto@exemplo.invalid');",
        papel="postgres",
    )
    yield nome
    subprocess.run(["docker", "rm", "-f", nome], capture_output=True)


def novo_projeto(banco, usuario, titulo="Projeto Teste") -> str:
    r = psql(banco, f"insert into projetos(titulo) values ('{titulo}') returning id;", usuario)
    return next(x for x in r.stdout.splitlines() if len(x) == 36 and x.count("-") == 4)


def test_usuario_so_ve_e_altera_os_proprios_projetos(banco):
    da_ana = novo_projeto(banco, ANA)
    assert saida(psql(banco, "select count(*) from projetos;", ANA)) == "1"
    assert saida(psql(banco, "select count(*) from projetos;", BETO)) == "0"
    # Beto não atualiza nem apaga o projeto da Ana (0 linhas afetadas, sem erro).
    assert (
        "UPDATE 0"
        in psql(banco, f"update projetos set titulo='x' where id='{da_ana}';", BETO).stdout
    )
    assert "DELETE 0" in psql(banco, f"delete from projetos where id='{da_ana}';", BETO).stdout
    # E não cria projeto em nome da Ana.
    r = psql(
        banco, f"insert into projetos(user_id,titulo) values ('{ANA}','x');", BETO, check=False
    )
    assert r.returncode != 0 and "row-level security" in r.stderr


def test_anon_nao_acessa_nada_do_assistente(banco):
    for tabela in (
        "projetos",
        "projeto_proponente",
        "projeto_secoes",
        "mensagens",
        "respostas",
        "uso_diario",
    ):
        r = psql(banco, f"select count(*) from {tabela};", papel="anon", check=False)
        assert r.returncode != 0, tabela


def test_secoes_historico_e_aprovacao(banco):
    p = novo_projeto(banco, ANA, "Com seções")
    psql(
        banco,
        f"insert into projeto_secoes(projeto_id,secao_id,rascunho) values ('{p}','justificativa','v1');",
        ANA,
    )
    psql(banco, f"update projeto_secoes set rascunho='v2' where projeto_id='{p}';", ANA)
    psql(banco, f"update projeto_secoes set aprovado=true where projeto_id='{p}';", ANA)
    assert (
        saida(
            psql(
                banco,
                f"select versao::text||aprovado::text from projeto_secoes where projeto_id='{p}';",
                ANA,
            )
        )
        == "2true"
    )
    # Editar depois de aprovar tira a aprovação e guarda a versão anterior.
    psql(banco, f"update projeto_secoes set rascunho='v3' where projeto_id='{p}';", ANA)
    assert (
        saida(
            psql(
                banco,
                f"select versao::text||aprovado::text from projeto_secoes where projeto_id='{p}';",
                ANA,
            )
        )
        == "3false"
    )
    hist = psql(
        banco,
        "select string_agg(rascunho||versao::text, ',' order by versao) from projeto_secoes_hist;",
        ANA,
    )
    assert saida(hist) == "v11,v22"
    # Outro usuário não lê o histórico nem as seções.
    assert saida(psql(banco, "select count(*) from projeto_secoes_hist;", BETO)) == "0"
    assert saida(psql(banco, "select count(*) from projeto_secoes;", BETO)) == "0"
    # O histórico é somente leitura para o usuário.
    r = psql(banco, "delete from projeto_secoes_hist;", ANA, check=False)
    assert r.returncode != 0


def test_mensagens_so_a_funcao_escreve_e_so_o_dono_le(banco):
    p = novo_projeto(banco, ANA, "Chat")
    escrita = f"insert into mensagens(projeto_id,secao_id,papel,conteudo) values ('{p}','ficha','usuario','{{}}');"
    assert psql(banco, escrita, ANA, check=False).returncode != 0
    psql(banco, escrita, papel="service_role")
    assert saida(psql(banco, f"select count(*) from mensagens where projeto_id='{p}';", ANA)) == "1"
    assert (
        saida(psql(banco, f"select count(*) from mensagens where projeto_id='{p}';", BETO)) == "0"
    )


def test_dados_pessoais_ficam_em_tabela_propria_isolada(banco):
    p = novo_projeto(banco, ANA, "Com proponente")
    psql(
        banco,
        f"insert into projeto_proponente(projeto_id,dados) values ('{p}','{{\"cpf\":\"000.000.000-00\"}}');",
        ANA,
    )
    assert (
        saida(psql(banco, f"select count(*) from projeto_proponente where projeto_id='{p}';", BETO))
        == "0"
    )
    r = psql(
        banco,
        f"insert into projeto_proponente(projeto_id,dados) values ('{p}','{{}}') on conflict do nothing;",
        BETO,
        check=False,
    )
    assert r.returncode != 0 or "INSERT 0 0" in r.stdout
    # A ficha (o que vai ao LLM) não tem coluna de dados pessoais.
    cols = psql(
        banco,
        "select column_name from information_schema.columns where table_name='projetos';",
        papel="postgres",
    ).stdout.split()
    assert not {"cpf", "cnpj", "proponente", "endereco", "telefone"} & set(cols)


def test_limite_diario_e_atomico_e_so_o_service_role_chama(banco):
    def chamar():
        return saida(
            psql(banco, f"select assistente_consumir_uso('{BETO}', 3);", papel="service_role")
        )

    assert [chamar() for _ in range(5)] == ["1", "2", "3", "-1", "-1"]
    assert (
        saida(
            psql(
                banco, f"select chamadas from uso_diario where user_id='{BETO}';", papel="postgres"
            )
        )
        == "3"
    )
    r = psql(banco, f"select assistente_consumir_uso('{BETO}', 3);", BETO, check=False)
    assert r.returncode != 0  # authenticated não pode se dar crédito nem consumir o de outro
    assert saida(psql(banco, "select count(*) from uso_diario;", ANA)) == "0"  # só vê o próprio


def test_criterios_do_edital_sao_somente_leitura_para_logados(banco):
    assert saida(psql(banco, "select count(*) from edital_criterios;", ANA)) == "0"
    r = psql(banco, "insert into edital_criterios(edital_id) values (1);", ANA, check=False)
    assert r.returncode != 0


def test_excluir_edital_nao_apaga_o_projeto(banco):
    psql(
        banco,
        "insert into editais(id,fonte,id_externo,url_origem,titulo,orgao_fomento,inscricao_fim)"
        " overriding system value values (9001,'t','x','u','Edital Teste A','o',now());",
        papel="postgres",
    )
    p = novo_projeto(banco, ANA, "Vinculado")
    psql(
        banco,
        f"update projetos set edital_id=9001, edital_titulo='Edital Teste A' where id='{p}';",
        ANA,
    )
    psql(banco, "delete from editais where id=9001;", papel="postgres")
    assert (
        saida(
            psql(
                banco,
                f"select coalesce(edital_id::text,'nulo')||'|'||edital_titulo from projetos where id='{p}';",
                ANA,
            )
        )
        == "nulo|Edital Teste A"
    )
