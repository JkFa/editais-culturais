"""Migração da Fase 2 do assistente num PostgreSQL real (Docker): RLS, constraints, admin e fila."""

import shutil
import uuid

import pytest
from test_assistente_sql import ANA, BETO, banco, novo_projeto, psql, saida  # noqa: F401

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker indisponível")

ADMIN = str(uuid.uuid4())
TABELAS = ("orcamento_itens", "cronograma_itens", "midia_itens", "equipe_membros")


def preparar_admin(banco):
    psql(
        banco,
        f"insert into auth.users(id,email) values ('{ADMIN}','admin@exemplo.invalid') on conflict do nothing;"
        f"insert into admins(user_id) values ('{ADMIN}') on conflict do nothing;",
        papel="postgres",
    )


def inserir_orcamento(banco, projeto, item="Cachê", **extra):
    colunas = {"projeto_id": projeto, "etapa": "producao", "item": item, **extra}
    cols = ",".join(colunas)
    vals = ",".join(f"'{v}'" if isinstance(v, str) else str(v) for v in colunas.values())
    return psql(
        banco,
        f"insert into orcamento_itens({cols}) values ({vals}) returning id;",
        papel="service_role",
    )


def test_dono_le_so_o_que_e_do_proprio_projeto_e_nao_escreve(banco):
    p = novo_projeto(banco, ANA, "Fase 2")
    inserir_orcamento(banco, p)
    psql(
        banco,
        f"insert into cronograma_itens(projeto_id,etapa,atividade,mes_inicio,mes_fim) values ('{p}','producao','Ensaios',1,2);",
        papel="service_role",
    )
    psql(
        banco,
        f"insert into equipe_membros(projeto_id,funcao) values ('{p}','Direção');",
        papel="service_role",
    )
    psql(
        banco,
        f"insert into midia_itens(projeto_id,peca) values ('{p}','Cartaz');",
        papel="service_role",
    )
    for t in TABELAS:
        assert (
            saida(psql(banco, f"select count(*) from {t} where projeto_id='{p}';", ANA)) == "1"
        ), t
        assert saida(psql(banco, f"select count(*) from {t};", BETO)) == "0", t  # RLS: Beto não vê
        # o usuário não grava direto: quem grava é a Edge Function (service_role)
        r = psql(banco, f"delete from {t} where projeto_id='{p}';", ANA, check=False)
        assert r.returncode != 0 and "permission denied" in r.stderr, t
        r = psql(banco, f"select count(*) from {t};", papel="anon", check=False)
        assert r.returncode != 0, t


def test_constraints_de_orcamento_cronograma_e_equipe(banco):
    p = novo_projeto(banco, ANA, "Constraints")

    def falha(sql):
        r = psql(banco, sql, papel="service_role", check=False)
        assert r.returncode != 0, sql

    falha(f"insert into orcamento_itens(projeto_id,etapa,item) values ('{p}','luxo','x');")
    falha(
        f"insert into orcamento_itens(projeto_id,etapa,item,quantidade) values ('{p}','producao','x',-1);"
    )
    falha(
        f"insert into orcamento_itens(projeto_id,etapa,item,valor_unitario) values ('{p}','producao','x',-5);"
    )
    falha(f"insert into orcamento_itens(projeto_id,etapa,item) values ('{p}','producao','');")
    falha(
        f"insert into cronograma_itens(projeto_id,etapa,atividade,mes_inicio,mes_fim) values ('{p}','producao','a',5,2);"
    )
    falha(
        f"insert into cronograma_itens(projeto_id,etapa,atividade,mes_inicio,mes_fim) values ('{p}','producao','a',0,2);"
    )
    falha(f"insert into equipe_membros(projeto_id,funcao) values ('{p}','');")
    # valor nulo é permitido: "valor a confirmar"
    inserir_orcamento(banco, p, "Item sem preço")
    assert (
        saida(
            psql(
                banco,
                f"select count(*) from orcamento_itens where projeto_id='{p}' and valor_unitario is null;",
                papel="service_role",
            )
        )
        == "1"
    )


def test_equipe_nao_tem_coluna_de_documento(banco):
    cols = psql(
        banco,
        "select column_name from information_schema.columns where table_name='equipe_membros';",
        papel="postgres",
    ).stdout.split()
    assert not [
        c
        for c in cols
        if any(x in c for x in ("cpf", "rg", "documento", "cnpj", "email", "telefone"))
    ], cols


def test_excluir_orcamento_solta_a_midia_e_excluir_projeto_apaga_tudo(banco):
    p = novo_projeto(banco, ANA, "Cascata")
    oid = saida(inserir_orcamento(banco, p, "Impressão de cartazes", etapa="divulgacao"))
    psql(
        banco,
        f"insert into midia_itens(projeto_id,peca,orcamento_item_id) values ('{p}','Cartaz','{oid}');",
        papel="service_role",
    )
    psql(banco, f"delete from orcamento_itens where id='{oid}';", papel="service_role")
    assert (
        saida(
            psql(
                banco,
                f"select count(*) from midia_itens where projeto_id='{p}' and orcamento_item_id is null;",
                papel="service_role",
            )
        )
        == "1"
    )
    psql(banco, f"delete from projetos where id='{p}';", ANA)
    for t in TABELAS:
        assert (
            saida(
                psql(banco, f"select count(*) from {t} where projeto_id='{p}';", papel="postgres")
            )
            == "0"
        ), t


def test_admins_so_veem_a_propria_linha_e_ninguem_se_promove(banco):
    preparar_admin(banco)
    assert saida(psql(banco, "select count(*) from admins;", ADMIN)) == "1"
    assert saida(psql(banco, "select count(*) from admins;", ANA)) == "0"
    assert saida(psql(banco, "select eh_admin();", ADMIN)) == "t"
    assert saida(psql(banco, "select eh_admin();", ANA)) == "f"
    r = psql(banco, f"insert into admins(user_id) values ('{ANA}');", ANA, check=False)
    assert r.returncode != 0 and "permission denied" in r.stderr
    r = psql(banco, "select eh_admin();", papel="anon", check=False)
    assert r.returncode != 0


def criar_edital_e_criterios(banco):
    psql(
        banco,
        "insert into editais(fonte,id_externo,url_origem,titulo,orgao_fomento,inscricao_fim) values ('t','crit-1','u','Edital com critérios','o', now() + interval '30 days') on conflict do nothing;",
        papel="postgres",
    )
    eid = saida(psql(banco, "select id from editais where id_externo='crit-1';", papel="postgres"))
    psql(
        banco,
        f"insert into edital_criterios(edital_id, teto_orcamento, percentuais_max, fonte_hash) values ({eid}, 100000, '{{\"divulgacao\": 0.15}}', 'abc') on conflict do nothing;",
        papel="service_role",
    )
    return eid


def test_criterios_todos_logados_leem_mas_so_admin_corrige(banco):
    preparar_admin(banco)
    eid = criar_edital_e_criterios(banco)
    assert (
        saida(
            psql(
                banco,
                f"select teto_orcamento::int from edital_criterios where edital_id={eid};",
                ANA,
            )
        )
        == "100000"
    )
    # usuário comum: nenhuma linha atualizada (RLS) ou permissão negada
    r = psql(
        banco, f"update edital_criterios set revisado=true where edital_id={eid};", ANA, check=False
    )
    assert r.returncode != 0 or "UPDATE 0" in r.stdout
    assert (
        saida(
            psql(
                banco,
                f"select revisado::text from edital_criterios where edital_id={eid};",
                papel="postgres",
            )
        )
        == "false"
    )
    # admin revisa
    psql(
        banco,
        f"update edital_criterios set revisado=true, revisado_em=now(), revisado_por='{ADMIN}', teto_orcamento=90000 where edital_id={eid};",
        ADMIN,
    )
    assert (
        saida(
            psql(
                banco,
                f"select revisado::text||teto_orcamento::int from edital_criterios where edital_id={eid};",
                papel="postgres",
            )
        )
        == "true90000"
    )
    # e não mexe no que é do worker (hash do documento, modelo, data da extração)
    r = psql(
        banco,
        f"update edital_criterios set fonte_hash='x' where edital_id={eid};",
        ADMIN,
        check=False,
    )
    assert r.returncode != 0 and "permission denied" in r.stderr
    # o admin também não apaga nem cria critérios
    assert (
        psql(
            banco, f"delete from edital_criterios where edital_id={eid};", ADMIN, check=False
        ).returncode
        != 0
    )


def test_criterios_rejeitam_valores_absurdos(banco):
    eid = criar_edital_e_criterios(banco)
    for sql in (
        f"update edital_criterios set teto_orcamento=-1 where edital_id={eid};",
        f"update edital_criterios set teto_orcamento=0 where edital_id={eid};",
        f"update edital_criterios set prazo_execucao_meses=0 where edital_id={eid};",
        f"update edital_criterios set criterios='{{}}'::jsonb where edital_id={eid};",
        f"update edital_criterios set percentuais_max='[]'::jsonb where edital_id={eid};",
    ):
        assert psql(banco, sql, papel="service_role", check=False).returncode != 0, sql


def test_lista_de_revisao_so_para_admin_e_ordenada_pelos_projetos_mais_usados(banco):
    preparar_admin(banco)
    eid = criar_edital_e_criterios(banco)
    psql(
        banco,
        "insert into editais(fonte,id_externo,url_origem,titulo,orgao_fomento,inscricao_fim) values ('t','crit-2','u','Edital popular','o', now() + interval '60 days') on conflict do nothing;",
        papel="postgres",
    )
    popular = saida(
        psql(banco, "select id from editais where id_externo='crit-2';", papel="postgres")
    )
    psql(
        banco,
        f"insert into edital_criterios(edital_id) values ({popular}) on conflict do nothing;",
        papel="service_role",
    )
    psql(
        banco,
        f"update edital_criterios set revisado=false where edital_id in ({eid},{popular});",
        papel="service_role",
    )
    for _ in range(2):
        psql(banco, f"insert into projetos(titulo, edital_id) values ('p', {popular});", ANA)
    psql(banco, f"insert into projetos(titulo, edital_id) values ('q', {popular});", BETO)
    linhas = psql(
        banco, "select edital_id::text||':'||projetos from admin_criterios_para_revisar();", ADMIN
    ).stdout.split()
    assert [x for x in linhas if ":" in x][:2] == [f"{popular}:3", f"{eid}:0"], (
        "mais projetos primeiro"
    )
    # usuário comum não vê nada (nem contagens de projetos dos outros); anônimo nem executa
    assert saida(psql(banco, "select count(*) from admin_criterios_para_revisar();", ANA)) == "0"
    assert (
        psql(
            banco, "select count(*) from admin_criterios_para_revisar();", papel="anon", check=False
        ).returncode
        != 0
    )
    # revisado sai da lista
    psql(banco, f"update edital_criterios set revisado=true where edital_id={popular};", ADMIN)
    assert (
        saida(
            psql(
                banco,
                f"select count(*) from admin_criterios_para_revisar() where edital_id={popular};",
                ADMIN,
            )
        )
        == "0"
    )


def test_fila_de_extracao_so_admin_enxerga_e_reprocessa(banco):
    preparar_admin(banco)
    eid = criar_edital_e_criterios(banco)
    psql(
        banco,
        f"insert into extracao_fila(edital_id,status) values ({eid},'erro') on conflict (edital_id) do update set status='erro';",
        papel="service_role",
    )
    assert saida(psql(banco, "select count(*) from extracao_fila;", ANA)) == "0"
    assert saida(psql(banco, "select count(*) from extracao_fila;", ADMIN)) == "1"
    psql(
        banco,
        f"update extracao_fila set status='pendente', tentativas=0 where edital_id={eid};",
        ADMIN,
    )
    assert (
        saida(
            psql(
                banco, f"select status from extracao_fila where edital_id={eid};", papel="postgres"
            )
        )
        == "pendente"
    )
    assert (
        psql(
            banco, f"insert into extracao_fila(edital_id) values ({eid});", ADMIN, check=False
        ).returncode
        != 0
    )
    r = psql(
        banco,
        f"update extracao_fila set status='lixo' where edital_id={eid};",
        papel="service_role",
        check=False,
    )
    assert r.returncode != 0  # check de status
    r = psql(banco, "select count(*) from extracao_fila;", papel="anon", check=False)
    assert r.returncode != 0


def test_migration_roda_duas_vezes_e_schema_completo_idempotente(banco):
    from pathlib import Path

    schema = Path(__file__).resolve().parent.parent / "supabase" / "schema.sql"
    psql(banco, schema.read_text(encoding="utf-8"), papel="postgres")
    psql(banco, schema.read_text(encoding="utf-8"), papel="postgres")
