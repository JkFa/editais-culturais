"""PR 3b: abertura x prorrogação x ato posterior, órgão que não é de cultura, número do edital e prazo que não vira a última data da tabela."""

import json
from datetime import datetime
from pathlib import Path

import pytest
from test_querido_diario import AGORA, ORIGEM, coletar, ler, todos_os_diarios  # noqa: F401

from scripts import amostra_recall_qd as ar
from worker.coletores import querido_diario as qd
from worker.fila import Candidato
from worker.modelos import Edital
from worker.prazos_texto import achar_prazo

RAIZ = Path(__file__).resolve().parent.parent


def dia(p):
    return p.fim.astimezone(qd.FUSO_PADRAO).date().isoformat()


# ── prazo: a data do pedido de inscrição, não a última data do cronograma ────────────────────


@pytest.mark.parametrize(
    "texto, esperado",
    [
        # Itaguaí (RJ), trecho real da amostra: o edital já tinha encerrado as inscrições, a "análise" é que ia até 09/11
        (
            "EDITAL PADRONIZADO CHAMAMENTO PÚBLICO 013/2026 Cronograma inscrições 26/10/2026 a 31/10/2026 Análise das inscrições e avaliação 03/11/2026 a 09/11/2026 Resultado 10/11/2026",
            "2026-10-31",
        ),
        # Morro do Chapéu (BA), trecho real: "Seleção 03/11 a 09/11" é fase, não inscrição
        (
            "Inscrições 13/10 a 01/11/2026 Seleção 03/11 a 09/11/2026 Resultado preliminar 10/11/2026",
            "2026-11-01",
        ),
        # prorrogação dentro do próprio quadro (Niterói): vale a última data do MESMO pedido de inscrição
        (
            "PRAZO PREVISTO Inscrições De 11/09/2026 a 26/10/2026 De 11/09/2026 a 09/11/2026",
            "2026-11-09",
        ),
        (
            "As inscrições para seleção de projetos culturais vão até 12/11/2026 pelo site.",
            "2026-11-12",
        ),
        (
            "As inscrições serão gratuitas e deverão ser realizadas até 23h59 do dia 15/11/2026, por formulário.",
            "2026-11-15",
        ),
    ],
)
def test_prazo_nao_vira_a_ultima_data_da_tabela(texto, esperado):
    assert dia(achar_prazo(texto)) == esperado


def test_analise_ou_recurso_das_inscricoes_nao_e_o_pedido_de_inscricao():
    assert (
        achar_prazo(
            "Resultado da análise das inscrições até 20/11/2026. Recurso das inscrições até 25/11/2026."
        )
        is None
    )


# ── número do edital ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "titulo, esperado",
    [
        ("EDITAL DE CHAMAMENTO PÚBLICO Nº 02/2026", {"2/2026"}),
        ("Edital Chamamento Público Nº002/2026 – Viva Delmiro", {"2/2026"}),
        ("CHAMAMENTO PÚBLICO Nº 01/2026 E Nº 02/2026 - POLÍTICA NACIONAL", {"1/2026", "2/2026"}),
        ("EDITAL Nº 070/2026 – POLÍTICA NACIONAL ALDIR BLANC", {"70/2026"}),
        ("Edital de Teatro", set()),
    ],
)
def test_numeros_do_edital(titulo, esperado):
    assert qd.numeros_do_edital(titulo) == esperado


# ── tipo do ato ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "contexto, esperado",
    [
        (
            "AVISO DE PRORROGAÇÃO EDITAL DE CHAMAMENTO PÚBLICO Nº 03/2026 fica prorrogado o prazo de inscrição",
            "prorrogacao",
        ),
        ("ADIAMENTO DAS INSCRIÇÕES EDITAL Nº 05/2026 nova data", "prorrogacao"),
        ("RETIFICAÇÃO EDITAL Nº 03/2026 onde se lê 10/11 leia-se 12/11", "retificacao"),
        ("ERRATA AO EDITAL Nº 03/2026 leia-se", "retificacao"),
        (
            "EDITAL DE CHAMAMENTO PÚBLICO Nº 03/2026 A Prefeitura torna público e abre as inscrições",
            "abertura",
        ),
        (
            "EDITAL RETIFICADO Nº 01/2026 O Município torna público o edital com as inscrições estarão abertas",
            "abertura",
        ),
    ],
)
def test_tipo_do_ato(contexto, esperado):
    assert qd.tipo_do_ato(contexto) == esperado


# ── cultura x outras áreas ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "titulo, trecho, esperado",
    [
        (
            "EDITAL SEMED RENOVAÇÃO CME Nº 01/2026",
            "Secretaria Municipal de Educação: eleição do conselho, inscrições das delegações. cultura",
            False,
        ),
        ("EDITAL Nº 03/2026", "Conselho Municipal de Saúde, inscrições de entidades", False),
        (
            "EDITAL SME 003/2026",
            "A Secretaria de Educação torna público o processo, com recursos de R$ 10.000,00",
            False,
        ),
        (
            "EDITAL DE CHAMAMENTO PÚBLICO Nº 02/2026 - PNAB",
            "Política Nacional Aldir Blanc de Fomento à Cultura: projetos culturais e artistas",
            True,
        ),
        (
            "EDITAL Nº 07/2026",
            "Secretaria de Educação e Cultura. Fomento às artes, artistas e fazedores de cultura da Política Nacional Aldir Blanc",
            True,
        ),
        ("EDITAL DE FOMENTO Nº 1/2026", "projetos de música, teatro e dança", True),
        ("EDITAL Nº 9/2026", "obras de saneamento", False),
    ],
)
def test_so_cultura_passa(titulo, trecho, esperado):
    assert qd.eh_cultural(titulo, trecho) is esperado


# ── passagens ────────────────────────────────────────────────────────────────────────────────

CORPO = (
    "A Prefeitura torna público o edital de apoio financeiro a projetos culturais da Política Nacional Aldir Blanc, no valor de R$ 100.000,00 para artistas. "
    "As inscrições serão realizadas de {de} a {ate}. " + "Demais regras do edital seguem. " * 10
)


def texto_do_diario(*blocos):
    return "DIÁRIO OFICIAL DO MUNICÍPIO DE EXEMPLO\n" + "\n[...]\n".join(blocos)


def test_prorrogacao_gera_passagem_com_o_novo_prazo_e_resultado_e_retificacao_nao():
    t = texto_do_diario(
        "AVISO DE PRORROGAÇÃO EDITAL DE CHAMAMENTO PÚBLICO Nº 03/2026 A Secretaria de Cultura torna público que fica prorrogado o prazo de inscrição do edital até 30/11/2026, para projetos culturais de artistas da Política Nacional Aldir Blanc.",
        "RESULTADO FINAL DO EDITAL DE CHAMAMENTO PÚBLICO Nº 01/2026 cultura. Inscrições de 01/11/2026 a 20/11/2026. R$ 5.000,00 apoio "
        + "x " * 60,
        "RETIFICAÇÃO EDITAL DE CHAMAMENTO PÚBLICO Nº 02/2026 cultura: onde se lê 10/11/2026 leia-se 25/11/2026 nas inscrições, artistas, fomento R$ 1.000,00.",
    )
    ps = qd.achar_passagens(t, AGORA)
    assert [(p.tipo, dia(p.prazo)) for p in ps] == [("prorrogacao", "2026-11-30")]
    assert qd.numeros_do_edital(ps[0].titulo) == {"3/2026"}


def test_o_mesmo_edital_citado_duas_vezes_vale_uma_so_e_conselho_de_educacao_fica_de_fora():
    edital = "EDITAL DE CHAMAMENTO PÚBLICO Nº 02/2026 " + CORPO.format(
        de="01/11/2026", ate="20/11/2026"
    )
    t = texto_do_diario(
        edital,
        edital.replace("Nº 02/2026", "Nº002/2026"),
        "EDITAL SEMED RENOVAÇÃO CME Nº 01/2026 Secretaria Municipal de Educação, cultura escolar, R$ 3.000,00. inscrições das delegações 11 a 18/11/2026 "
        + "x " * 60,
    )
    ps = qd.achar_passagens(t, AGORA)
    assert (
        len(ps) == 1
        and ps[0].tipo == "abertura"
        and qd.numeros_do_edital(ps[0].titulo) == {"2/2026"}
    )


def test_prazo_que_ja_encerrou_numa_tabela_nao_gera_passagem():
    t = texto_do_diario(
        "EDITAL PADRONIZADO CHAMAMENTO PÚBLICO 013/2026 A Prefeitura torna público o fomento cultural de artistas, R$ 8.000,00. Cronograma inscrições 26/10/2026 a 31/10/2026 Análise das inscrições e avaliação 03/11/2026 a 09/11/2026 "
        + "x " * 60
    )
    assert qd.achar_passagens(t, AGORA) == [], "as inscrições acabaram em 31/10; 09/11 é a análise"


# ── coleta: prorrogação de edital que o portal já tem ────────────────────────────────────────


def gaz(tid, nome, uf):
    return {"territory_id": tid, "territory_name": nome, "state_code": uf, "date": "2026-11-06", "txt_url": f"https://data.queridodiario.ok.org.br/{tid}/a.txt",
            "url": f"https://data.queridodiario.ok.org.br/{tid}/2026-11-06", "excerpts": ["torna público a prorrogação do prazo de inscrição do Edital"]}  # fmt: skip


PRORROGACAO = "AVISO DE PRORROGAÇÃO EDITAL DE CHAMAMENTO PÚBLICO Nº 03/2026 A Secretaria de Cultura torna público que fica prorrogado o prazo de inscrição do edital até 30/11/2026, para projetos culturais de artistas da Política Nacional Aldir Blanc, apoio R$ 10.000,00."


def existente(
    fonte="mapas:sp",
    fim="2026-11-13T02:59:59+00:00",
    titulo="Edital de Chamamento Público Nº 03/2026 - Exemplo PNAB",
):
    return Edital(
        fonte=fonte,
        id_externo="1",
        url_origem="https://exemplo.sp.gov.br/edital-3",
        titulo=titulo,
        orgao_fomento="Município de Exemplo",
        uf="SP",
        inscricao_fim=datetime.fromisoformat(fim),
    )


def rodar(existentes):
    g = gaz("3500001", "Exemplo", "SP")
    return coletar([g], textos={g["txt_url"]: texto_do_diario(PRORROGACAO)}, existentes=existentes)


def test_prorrogacao_de_edital_do_portal_vira_aviso_com_os_dois_prazos_e_o_link_do_edital():
    r = rodar([existente()])
    assert len(r.candidatos) == 1
    c = r.candidatos[0]
    assert (
        c.titulo.startswith("Aviso de prorrogação: ")
        and c.chave.startswith("qd-prorrogacao:")
        and c.campos["aviso"] is True
    )
    assert c.link_oficial == "https://exemplo.sp.gov.br/edital-3"
    assert "12/11/2026" in c.motivo and "30/11/2026" in c.motivo and "mapas:sp" in c.motivo
    corpo = c.corpo()
    assert (
        "Feche esta Issue" in corpo
        and "Não use a etiqueta `aprovado`" in corpo
        and "Como aprovar" not in corpo
    )


def test_prorrogacao_de_edital_automatico_ou_ja_refletida_nao_gera_aviso():
    auto = coletar_aviso(existente(fonte="publicacao-automatica"))
    assert auto.candidatos == [] and auto.descartes["prorrogacao_de_automatico"] == 1, (
        "a releitura diária da página oficial já atualiza"
    )
    refletida = coletar_aviso(existente(fim="2026-12-01T02:59:59+00:00"))
    assert refletida.candidatos == [] and refletida.descartes["prorrogacao_ja_refletida"] == 1


def coletar_aviso(e):
    return rodar([e])


def test_prorrogacao_de_edital_que_o_portal_nao_tem_vira_candidato_normal_com_o_prazo_novo():
    r = rodar([existente(titulo="Edital de Teatro de Outra Cidade")])
    assert (
        len(r.candidatos) == 1
        and not r.candidatos[0].campos.get("aviso")
        and r.candidatos[0].campos["inscricao_fim"] == "2026-11-30"
    )
    assert r.candidatos[0].chave.startswith("qd:")


# ── amostra: um edital, uma vez ──────────────────────────────────────────────────────────────


def test_amostra_junta_o_mesmo_edital_citado_de_jeitos_diferentes_e_fica_com_o_prazo_mais_novo():
    a = {
        "titulo": "EDITAL DE CHAMAMENTO PÚBLICO Nº 02/2026",
        "uf": "AL",
        "municipio": "Delmiro Gouveia",
        "prazo": "2026-11-13",
    }
    b = {
        "titulo": "Edital Chamamento Público Nº002/2026 – Viva Delmiro Gouveia",
        "uf": "AL",
        "municipio": "Delmiro Gouveia",
        "prazo": "2026-11-13",
    }
    c = {
        "titulo": "CHAMAMENTO PÚBLICO Nº 06/2026",
        "uf": "SP",
        "municipio": "Itapuí",
        "prazo": "2026-11-19",
    }
    d = {
        "titulo": "EDITAL Nº 070/2026 – PNAB",
        "uf": "SP",
        "municipio": "Itapuí",
        "prazo": "2026-11-19",
    }
    assert ar._chave(a) == ar._chave(b)
    assert len({ar._chave(x) for x in (a, b, c, d)}) == 3, (
        "Itapuí tem dois números diferentes: dois editais"
    )
    assert ar._chave({**a, "uf": "SP"}) != ar._chave(a)


def test_amostra_real_de_hoje_perde_os_ruidos_ao_passar_pelo_classificador_novo():
    """Os 3 itens suspeitos da amostra real de 09/11/2026 não passam mais: educação/conselho, quadro de cronograma e duplicatas."""
    itens = [
        i
        for i in json.loads(
            (RAIZ / "worker" / "referencia" / "amostra_recall.json").read_text(encoding="utf-8")
        )["itens"]
        if i.get("origem") == "querido-diario"
    ]
    chaves = [ar._chave(i) for i in itens]
    assert len(chaves) - len(set(chaves)) >= 3, (
        "havia duplicatas (Maravilha, Delmiro Gouveia, Morro do Chapéu) contadas duas vezes"
    )
    assert not qd.eh_cultural(
        "EDITAL SEMED RENOVAÇÃO CME Nº 01/2026",
        "Secretaria Municipal de Educação inscrições das delegações e demais segmentos cultura",
    )
    assert (
        dia(
            achar_prazo(
                "inscrições 26/10/2026 a 31/10/2026 Análise das inscrições e avaliação 03/11/2026 a 09/11/2026"
            )
        )
        == "2026-10-31"
    )


def test_candidato_de_aviso_nao_afeta_o_corpo_dos_candidatos_normais():
    c = Candidato(chave="x", titulo="t", link_oficial="https://x", origem="o")
    assert "Como aprovar" in c.corpo() and "Feche esta Issue" not in c.corpo()
