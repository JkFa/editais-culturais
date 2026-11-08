"""Extração de critérios: verificação de trechos, validação, fila e o fluxo completo, sem rede e sem LLM de verdade.

Os PDFs são editais públicos reais (tests/fixtures/extracao/ORIGEM.json). As respostas do "modelo" são
simuladas com citações literais desses documentos e com alucinações plantadas, que NÃO podem chegar ao banco.
"""

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from scripts.extrair_criterios import executar as ex
from scripts.extrair_criterios.banco import Banco
from scripts.extrair_criterios.documento import SemDocumento, documento_de_bytes, paginas_de_pdf
from scripts.extrair_criterios.esquema import SCHEMA_GEMINI, RespostaLLM
from scripts.extrair_criterios.extracao import (
    ErroExtracao,
    dividir,
    extrair,
    fundir,
    validar_e_verificar,
)
from scripts.extrair_criterios.fila import planejar, url_do_documento
from scripts.extrair_criterios.llm import ErroLlm, ProvedorGemini
from scripts.extrair_criterios.prompt import montar_prompt
from scripts.extrair_criterios.texto import normalizar, slug, trecho_comprova_numero, trecho_existe
from worker.persistencia import ConfigSupabase
from worker.web import ClienteWeb

FIX = Path(__file__).parent / "fixtures" / "extracao"
PELOTAS = (FIX / "cultbr_012_2026_apoio.pdf").read_bytes()
PREMIACAO = (FIX / "cultbr_013_2026_premiacao.pdf").read_bytes()
DIVINO = (FIX / "es_divino_sao_lourenco_04_2026.pdf").read_bytes()
AGORA = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

T_TETO = "observado o valor máximo de R$ 30.000,00 (trinta mil reais) por projeto selecionado"
T_PRAZO = "Os projetos apresentados deverão ser executados em até 12 (doze) meses após a assinatura do Termo de Execução"
T_ACESS = "Os projetos devem contar com pelo menos uma medida de acessibilidade (seja física, atitudinal e/ou comunicacional)"
T_PLANILHA = "O agente cultural deve preencher a planilha orçamentária, constante no Anexo 3"
T_FORM = "O agente cultural deve preencher o Anexo 2 - Formulário de Inscrição/Plano de Trabalho"


def resposta_pelotas(**extra):
    base = {
        "criterios": [],
        "teto_orcamento": {"valor": 30000, "trecho": T_TETO, "pagina": 2},
        "prazo_execucao_meses": {"valor": 12, "trecho": T_PRAZO, "pagina": 6},
        "exige_acessibilidade": {"valor": True, "trecho": T_ACESS, "pagina": 8},
        "campos_exigidos": [
            {
                "nome": "Formulário de Inscrição/Plano de Trabalho",
                "secao": None,
                "trecho": T_FORM,
                "pagina": 6,
            },
            {
                "nome": "Planilha orçamentária",
                "secao": "orcamento",
                "trecho": T_PLANILHA,
                "pagina": 6,
            },
        ],
        "observacoes": "Os critérios de seleção estão em anexo separado.",
    }
    return {**base, **extra}


class ProvedorFalso:
    """Devolve respostas prontas, na ordem; guarda os prompts para conferir o que foi enviado."""

    modelo = "modelo-falso"

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.prompts: list[str] = []

    async def gerar(self, prompt, schema):
        self.prompts.append(prompt)
        r = self.respostas[min(len(self.prompts) - 1, len(self.respostas) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def doc(dados=PELOTAS, url="https://exemplo.test/edital.pdf"):
    return documento_de_bytes(url, dados, "application/pdf")


# ── texto e verificação de trechos ───────────────────────────────────────────────────────────


def test_normalizar_ignora_acento_caixa_pontuacao_e_hifenizacao():
    assert normalizar("Orça-\nmento  MÁXIMO, de R$ 30.000,00!") == normalizar(
        "orcamento maximo de r$ 30 000 00"
    )
    assert normalizar("a­b") == "ab"


def test_trecho_existe_com_quebra_de_linha_e_reticencias_em_ordem():
    texto = normalizar(
        "1. O valor máximo\nde R$ 30.000,00 (trinta mil reais)\npor projeto selecionado, conforme o Anexo 1."
    )
    assert trecho_existe("O valor máximo de R$ 30.000,00", texto)
    assert trecho_existe("o valor máximo … por projeto selecionado", texto)
    assert not trecho_existe("por projeto selecionado … o valor máximo", texto), (
        "fragmentos fora de ordem"
    )
    assert not trecho_existe("o valor máximo é de R$ 50.000,00", texto)
    assert not trecho_existe("valor", texto), "trecho curto demais prova pouco"
    assert not trecho_existe("   ", texto)


@pytest.mark.parametrize(
    "valor, trecho, percentual, esperado",
    [
        (30000, "valor máximo de R$ 30.000,00 por projeto", False, True),
        (30000, "até 30 mil reais", False, True),
        (1500000, "R$ 1,5 milhão", False, True),
        (1500000, "R$ 1,5 milhões", False, True),
        (10457.14, "no valor individual de R$ 10.457,14", False, True),
        (99999, "valor máximo de R$ 30.000,00", False, False),
        (0.15, "divulgação limitada a 15% do valor", True, True),
        (0.15, "divulgação limitada a 15 por cento", True, True),
        (0.1, "divulgação limitada a 15%", True, False),
        (0.05, "percentual mínimo de 5% para pessoas com deficiência", True, True),
    ],
)
def test_numero_do_campo_precisa_estar_no_trecho(valor, trecho, percentual, esperado):
    assert trecho_comprova_numero(valor, trecho, percentual=percentual) is esperado


def test_slug_de_criterio():
    assert slug("Mérito cultural e artístico") == "merito_cultural_e_artistico"
    assert slug("!!!") == "criterio"


# ── documento ────────────────────────────────────────────────────────────────────────────────


def test_pdf_real_vira_paginas_com_texto():
    d = doc()
    assert d.tipo == "pdf" and len(d.paginas) == 17
    assert "valor máximo de R$ 30.000,00" in d.paginas[1].replace("\n", " ")  # página 2
    assert len(d.sha256) == 64


def test_pdf_escaneado_sem_texto_vira_sem_documento():
    import io

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(SemDocumento, match="escaneado"):
        documento_de_bytes("https://x.test/a.pdf", buf.getvalue(), "application/pdf")


def test_pagina_html_vira_uma_pagina_e_html_curto_nao_serve():
    html = (
        "<html><body><main><h1>Edital</h1><p>"
        + ("Texto do edital com regras. " * 40)
        + "</p></main></body></html>"
    )
    d = documento_de_bytes("https://x.test/edital", html.encode(), "text/html")
    assert d.tipo == "html" and len(d.paginas) == 1
    with pytest.raises(SemDocumento, match="JavaScript"):
        documento_de_bytes("https://x.test/e", b"<html><body>oi</body></html>", "text/html")


def test_dividir_agrupa_paginas_e_corta_pagina_gigante():
    partes = dividir(doc(), limite=15_000)
    assert len(partes) >= 3
    assert [n for parte in partes for n, _ in parte][:2] == [1, 2]
    assert all(sum(len(t) for _, t in p) <= 15_000 + 100 for p in partes if len(p) > 1)
    grande = documento_de_bytes(
        "https://x.test/g", ("<p>" + "palavra " * 5000 + "</p>").encode(), "text/html"
    )
    assert len(dividir(grande, limite=10_000)) >= 4


def test_prompt_marca_paginas_e_pede_null_e_trecho():
    p = montar_prompt([(3, "texto A"), (4, "texto B")], "Edital X")
    assert "=== PÁGINA 3 ===" in p and "=== PÁGINA 4 ===" in p and "EDITAL: Edital X" in p
    for regra in (
        "EXATAMENTE como estão no texto",
        "use null",
        "NUNCA invente",
        "LITERALMENTE",
        "no máximo 200 caracteres",
    ):
        assert regra in p
    assert "NÃO é o valor total do edital" in p


# ── extração (modelo simulado) ───────────────────────────────────────────────────────────────


def rodar(coro):
    return asyncio.run(coro)


def test_extracao_valida_grava_o_que_tem_trecho_e_numero():
    provedor = ProvedorFalso(*[resposta_pelotas()] + [{}] * 5)
    r = rodar(extrair(doc(), provedor, "Edital 012/2026"))
    c = r.campos
    assert (
        c["teto_orcamento"] == 30000.0
        and c["prazo_execucao_meses"] == 12
        and c["exige_acessibilidade"] is True
    )
    assert c["campos_exigidos"] == [
        {"nome": "Formulário de Inscrição/Plano de Trabalho", "secao": None},
        {"nome": "Planilha orçamentária", "secao": "orcamento"},
    ]
    assert c["evidencias"]["teto_orcamento"]["pagina"] == 2
    assert c["evidencias"]["prazo_execucao_meses"]["trecho"].startswith(
        "Os projetos apresentados deverão"
    )
    assert (
        c["criterios"] == [] and c["contrapartida_exigida"] is None and c["percentuais_max"] == {}
    )
    assert r.descartes == [] and not r.vazio
    assert len(provedor.prompts) == r.partes >= 3, "o documento grande é lido em partes"
    assert "Edital 012/2026" in provedor.prompts[0] and "=== PÁGINA 1 ===" in provedor.prompts[0]


def test_ALUCINACOES_nao_passam():
    ruins = resposta_pelotas(
        teto_por_proponente={
            "valor": 500000,
            "trecho": "cada proponente poderá receber até R$ 500.000,00 no total",
            "pagina": 3,
        },  # trecho inventado
        percentuais_max=[
            {
                "etapa": "divulgacao",
                "percentual": 0.15,
                "trecho": "A divulgação não poderá exceder 15% do valor do projeto",
                "pagina": 5,
            }
        ],  # trecho inventado
        percentuais_min=[
            {
                "etapa": "acessibilidade",
                "percentual": 0.2,
                "trecho": "O percentual mínimo de 5% para pessoas com deficiência",
                "pagina": 7,
            }
        ],  # trecho real, número errado
        rubricas_vedadas=[
            {
                "rubrica": "Bebidas alcoólicas",
                "trecho": "é vedado o pagamento de bebidas alcoólicas com recursos do edital",
                "pagina": 4,
            }
        ],
        limites_caracteres=[
            {
                "secao": "justificativa",
                "limite": 3000,
                "trecho": "A justificativa terá no máximo 3.000 caracteres",
                "pagina": 6,
            }
        ],
        criterios=[
            {
                "nome": "Mérito cultural",
                "descricao": "x",
                "peso": 3,
                "nota_max": 10,
                "trecho_edital": "O mérito cultural vale até 10 pontos",
                "pagina": 12,
            }
        ],
        contrapartida_exigida={
            "valor": "Duas oficinas gratuitas",
            "trecho": "o proponente deverá oferecer duas oficinas gratuitas",
            "pagina": 2,
        },
    )
    ruins["teto_orcamento"] = {
        "valor": 99999,
        "trecho": T_TETO,
        "pagina": 2,
    }  # trecho real, número que não está nele
    r = rodar(extrair(doc(), ProvedorFalso(ruins, *[{}] * 5)))
    c = r.campos
    assert c["teto_orcamento"] is None and "teto_orcamento" not in c["evidencias"]
    assert (
        c["teto_por_proponente"] is None
        and c["percentuais_max"] == {}
        and c["percentuais_min"] == {}
    )
    assert c["rubricas_vedadas"] == [] and c["limites_caracteres"] == {} and c["criterios"] == []
    assert c["contrapartida_exigida"] is None
    assert len(r.descartes) == 8, r.descartes
    assert any("não aparece no trecho" in d for d in r.descartes) and any(
        "trecho não encontrado" in d for d in r.descartes
    )
    # o que é verdade continua valendo
    assert c["prazo_execucao_meses"] == 12 and c["exige_acessibilidade"] is True


def test_nenhum_trecho_gravado_deixa_de_existir_no_texto_do_edital():
    mistura = resposta_pelotas(
        criterios=[
            {
                "nome": "Itens de acessibilidade",
                "descricao": None,
                "peso": None,
                "nota_max": None,
                "trecho_edital": T_ACESS,
                "pagina": 8,
            },
            {
                "nome": "Critério inventado",
                "descricao": None,
                "peso": None,
                "nota_max": None,
                "trecho_edital": "o júri valorizará a originalidade do projeto",
                "pagina": 9,
            },
        ]
    )
    d = doc()
    r = rodar(extrair(d, ProvedorFalso(mistura, *[{}] * 5)))
    texto = normalizar(d.texto)
    gravados = [c["trecho_edital"] for c in r.campos["criterios"]] + [
        e["trecho"] for e in r.campos["evidencias"].values()
    ]
    assert len(gravados) >= 5 and [c["nome"] for c in r.campos["criterios"]] == [
        "Itens de acessibilidade"
    ]
    assert all(trecho_existe(t, texto) and len(t) <= 200 for t in gravados)


def test_trecho_longo_e_cortado_em_200_e_pagina_fora_do_documento_vira_null():
    longo = T_TETO + " conforme o Anexo 1. " * 0
    r = validar_e_verificar(
        RespostaLLM.model_validate(
            {
                "teto_orcamento": {"valor": 30000, "trecho": longo, "pagina": 999},
                "criterios": [
                    {
                        "nome": "Critério com texto longo",
                        "trecho_edital": "3.2. O valor global disponível para este Edital é de R$ 180.000,00 (cento e oitenta mil reais), destinado ao apoio de até 6 (seis) projetos, observado o valor máximo de R$ 30.000,00 (trinta mil reais) por projeto selecionado, conforme o Anexo 1.",
                        "pagina": 2,
                    }
                ],
            }
        ),
        doc(),
    )
    assert r.campos["evidencias"]["teto_orcamento"]["pagina"] is None
    assert (
        len(r.campos["criterios"][0]["trecho_edital"]) == 200
        and r.campos["criterios"][0]["pagina"] == 2
    )


def test_pesos_em_fracao_que_nao_somam_1_sao_removidos_e_ids_nao_colidem():
    t = "observado o valor máximo de R$ 30.000,00 (trinta mil reais) por projeto selecionado"
    crit = lambda nome, peso: {"nome": nome, "peso": peso, "trecho_edital": t, "pagina": 2}  # noqa: E731
    r = validar_e_verificar(
        RespostaLLM.model_validate(
            {"criterios": [crit("Mérito", 0.5), crit("Mérito!", 0.2), crit("Impacto", 0.2)]}
        ),
        doc(),
    )
    assert [c["peso"] for c in r.campos["criterios"]] == [None, None, None]
    assert any("pesos em fração somam 0.90" in d for d in r.descartes)
    assert len({c["id"] for c in r.campos["criterios"]}) == 3
    ok = validar_e_verificar(
        RespostaLLM.model_validate({"criterios": [crit("Mérito", 0.6), crit("Impacto", 0.4)]}),
        doc(),
    )
    assert [c["peso"] for c in ok.campos["criterios"]] == [0.6, 0.4]
    pontos = validar_e_verificar(
        RespostaLLM.model_validate({"criterios": [crit("Mérito", 30), crit("Impacto", 70)]}), doc()
    )
    assert [c["peso"] for c in pontos.campos["criterios"]] == [30, 70], (
        "pesos em pontos não precisam somar 1"
    )


def test_valores_invalidos_sao_descartados_sem_derrubar_o_resto():
    r = validar_e_verificar(
        RespostaLLM.model_validate(
            resposta_pelotas(
                teto_por_proponente={"valor": -5, "trecho": T_TETO, "pagina": 2},
                percentuais_max=[
                    {"etapa": "inexistente", "percentual": 0.1, "trecho": T_TETO},
                    {"etapa": "divulgacao", "percentual": 15, "trecho": T_TETO},
                ],
                limites_caracteres=[{"secao": "metas", "limite": 5, "trecho": T_TETO}],
                campos_exigidos=[{"nome": "x", "secao": "orcamento", "trecho": T_PLANILHA}],
                prazo_execucao_meses={"valor": 999, "trecho": T_PRAZO},
            )
        ),
        doc(),
    )
    assert r.campos["teto_por_proponente"] is None and r.campos["prazo_execucao_meses"] is None
    assert (
        r.campos["percentuais_max"] == {}
        and r.campos["limites_caracteres"] == {}
        and r.campos["campos_exigidos"] == []
    )
    assert r.campos["teto_orcamento"] == 30000.0


def test_prazo_por_extenso_vale_se_o_trecho_diz_o_numero_em_palavras():
    r = validar_e_verificar(
        RespostaLLM.model_validate(
            {
                "prazo_execucao_meses": {
                    "valor": 12,
                    "trecho": "executados em até 12 (doze) meses após a assinatura do Termo de Execução",
                }
            }
        ),
        doc(),
    )
    assert r.campos["prazo_execucao_meses"] == 12


def test_resposta_com_forma_errada_derruba_a_extracao_com_erro():
    with pytest.raises(ErroExtracao, match="fora do esquema"):
        rodar(extrair(doc(), ProvedorFalso({"criterios": "não é lista"})))


def test_fusao_das_partes_primeiro_valor_vence_e_listas_sem_repeticao():
    a = RespostaLLM.model_validate(
        {
            "teto_orcamento": {"valor": 1, "trecho": "x" * 20},
            "rubricas_vedadas": [{"rubrica": "Bebida", "trecho": "t" * 20}],
        }
    )
    b = RespostaLLM.model_validate(
        {
            "teto_orcamento": {"valor": 2, "trecho": "y" * 20},
            "rubricas_vedadas": [
                {"rubrica": "bebida", "trecho": "u" * 20},
                {"rubrica": "Brinde", "trecho": "v" * 20},
            ],
            "observacoes": "nota",
        }
    )
    f = fundir([a, b])
    assert (
        f.teto_orcamento.valor == 1
        and [x.rubrica for x in f.rubricas_vedadas] == ["Bebida", "Brinde"]
        and f.observacoes == "nota"
    )


def test_documento_enorme_e_lido_so_ate_o_limite_de_partes():
    grande = documento_de_bytes(
        "https://x.test/g", ("<p>" + "palavra de edital " * 20000 + "</p>").encode(), "text/html"
    )
    provedor = ProvedorFalso({})
    r = rodar(extrair(grande, provedor))
    assert r.truncado and len(provedor.prompts) == 8


def test_html_nao_manda_numero_de_pagina_para_o_modelo_nem_guarda():
    html = (
        "<html><body><main><p>"
        + ("Os projetos devem contar com medidas de acessibilidade para todos. " * 20)
        + "</p></main></body></html>"
    )
    d = documento_de_bytes("https://x.test/e", html.encode(), "text/html")
    r = rodar(
        extrair(
            d,
            ProvedorFalso(
                {
                    "exige_acessibilidade": {
                        "valor": True,
                        "trecho": "Os projetos devem contar com medidas de acessibilidade para todos",
                        "pagina": 1,
                    }
                }
            ),
        )
    )
    assert (
        r.campos["exige_acessibilidade"] is True
        and r.campos["evidencias"]["exige_acessibilidade"]["pagina"] is None
    )


def test_o_esquema_do_gemini_cobre_todos_os_campos_do_banco():
    assert set(SCHEMA_GEMINI["properties"]) == {
        "criterios", "teto_orcamento", "teto_por_proponente", "percentuais_max", "percentuais_min", "rubricas_vedadas",
        "limites_caracteres", "campos_exigidos", "contrapartida_exigida", "prazo_execucao_meses", "exige_acessibilidade", "observacoes",
    }  # fmt: skip


# ── fila ─────────────────────────────────────────────────────────────────────────────────────


def edital(i, **extra):
    return {
        "id": i,
        "titulo": f"Edital {i}",
        "url_origem": f"https://exemplo.test/e{i}",
        "url_edital_pdf": f"https://exemplo.test/e{i}.pdf",
        "inscricao_fim": f"2026-12-{10 + i:02d}T00:00:00+00:00",
        **extra,
    }


def test_planejar_enfileira_novos_prioriza_quem_tem_projeto_e_respeita_o_maximo():
    eds = [edital(i) for i in range(1, 6)]
    p = planejar(eds, {}, {}, Counter({4: 3, 2: 1}), AGORA, 3)
    assert p.enfileirar == [1, 2, 3, 4, 5]
    assert [e["id"] for e in p.processar] == [
        4,
        2,
        1,
    ]  # mais projetos primeiro, depois o prazo mais próximo
    assert p.elegiveis == 5 and p.restantes == 2
    assert planejar(eds, {}, {}, Counter(), AGORA, 0).processar == []


def test_planejar_nao_reprocessa_ok_recente_mas_reconfere_url_mudada_erro_travado_e_velhos():
    ontem, antigo = AGORA - timedelta(days=1), AGORA - timedelta(days=40)
    fila = {
        1: {"status": "ok", "tentativas": 0, "atualizado_em": ontem},
        2: {"status": "ok", "tentativas": 0, "atualizado_em": ontem},  # url mudou
        3: {"status": "ok", "tentativas": 0, "atualizado_em": antigo},  # 30 dias: reconfere
        4: {"status": "erro", "tentativas": 1, "atualizado_em": ontem},  # tenta de novo
        5: {"status": "erro", "tentativas": 3, "atualizado_em": ontem},  # desistiu
        6: {
            "status": "processando",
            "tentativas": 0,
            "atualizado_em": AGORA - timedelta(minutes=10),
        },  # outro processo
        7: {
            "status": "processando",
            "tentativas": 0,
            "atualizado_em": AGORA - timedelta(hours=5),
        },  # travou
        8: {"status": "sem_documento", "tentativas": 0, "atualizado_em": ontem},
        9: {
            "status": "pendente",
            "tentativas": 0,
            "atualizado_em": ontem,
        },  # "reprocessar" da tela de revisão
    }
    criterios = {
        1: {"fonte_url": url_do_documento(edital(1))},
        2: {"fonte_url": "https://exemplo.test/antigo.pdf"},
    }
    p = planejar([edital(i) for i in range(1, 10)], fila, criterios, Counter(), AGORA, 20)
    assert sorted(e["id"] for e in p.processar) == [2, 3, 4, 7, 9]
    assert p.enfileirar == []


def test_url_do_documento_prefere_o_pdf():
    assert (
        url_do_documento({"url_edital_pdf": "https://a/p.pdf", "url_origem": "https://a/pagina"})
        == "https://a/p.pdf"
    )
    assert (
        url_do_documento({"url_edital_pdf": None, "url_origem": "https://a/pagina"})
        == "https://a/pagina"
    )
    assert url_do_documento({}) is None


# ── fluxo completo: Supabase, web e modelo simulados ─────────────────────────────────────────


class PostgrestFalso:
    """PostgREST em memória: o bastante para a fila e os critérios."""

    def __init__(self, editais):
        self.editais = editais
        self.fila: dict[int, dict] = {}
        self.criterios: dict[int, dict] = {}
        self.projetos: list[dict] = []
        self.chamadas: list[str] = []

    def transporte(self):
        return httpx.MockTransport(self.tratar)

    def tratar(self, req: httpx.Request) -> httpx.Response:
        caminho = req.url.path.rsplit("/", 1)[-1]
        q = dict(req.url.params)
        self.chamadas.append(f"{req.method} {caminho}")
        corpo = json.loads(req.content) if req.content else None
        desl, lim = int(q.get("offset", 0)), int(q.get("limit", 1000))
        if req.method == "GET":
            fontes = {
                "editais_abertos": self.editais,
                "extracao_fila": list(self.fila.values()),
                "edital_criterios": list(self.criterios.values()),
                "projetos": self.projetos,
            }
            return httpx.Response(200, json=fontes[caminho][desl : desl + lim])
        if req.method == "POST" and caminho == "extracao_fila":
            for x in corpo:
                self.fila.setdefault(
                    x["edital_id"],
                    {
                        "edital_id": x["edital_id"],
                        "status": "pendente",
                        "tentativas": 0,
                        "ultimo_erro": None,
                        "atualizado_em": AGORA.isoformat(),
                    },
                )
            return httpx.Response(201)
        if req.method == "POST" and caminho == "edital_criterios":
            self.criterios[corpo["edital_id"]] = corpo
            return httpx.Response(201)
        if req.method == "PATCH" and caminho == "extracao_fila":
            eid = int(q["edital_id"].removeprefix("eq."))
            self.fila[eid].update(corpo)
            return httpx.Response(204)
        return httpx.Response(404)


def web_falso(arquivos: dict[str, bytes | int]):
    """ClienteWeb com transporte falso: URL -> bytes do PDF, ou um status HTTP de erro."""
    pedidos: list[str] = []

    def tratar(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(404)
        pedidos.append(url)
        r = arquivos.get(url, 404)
        if isinstance(r, int):
            return httpx.Response(r)
        return httpx.Response(200, content=r, headers={"content-type": "application/pdf"})

    async def sem_espera(_):
        return None

    return ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera), pedidos


def montar(editais, arquivos, *respostas):
    pg = PostgrestFalso(editais)
    banco = Banco(ConfigSupabase("https://sb.test", "chave"), transporte=pg.transporte())
    web, pedidos = web_falso(arquivos)
    return pg, banco, web, pedidos, ProvedorFalso(*respostas)


def rodar_tudo(pg, banco, web, provedor, **kw):
    async def corrida():
        async with web:
            try:
                return await ex.executar(banco, web, provedor, agora=AGORA, **kw)
            finally:
                await banco.fechar()

    return asyncio.run(corrida())


VAZIA = {}


def test_fluxo_completo_grava_nao_revisado_com_hash_modelo_e_evidencias():
    eds = [edital(1)]
    pg, banco, web, pedidos, prov = montar(
        eds, {"https://exemplo.test/e1.pdf": PELOTAS}, resposta_pelotas(), *[VAZIA] * 6
    )
    r = rodar_tudo(pg, banco, web, prov, max_editais=20)
    assert (r.processados, r.ok, r.erro, r.sem_documento, r.restantes) == (1, 1, 0, 0, 0)
    linha = pg.criterios[1]
    assert (
        linha["revisado"] is False
        and linha["revisado_por"] is None
        and linha["modelo_llm"] == "modelo-falso"
    )
    assert linha["fonte_url"] == "https://exemplo.test/e1.pdf" and len(linha["fonte_hash"]) == 64
    assert linha["teto_orcamento"] == 30000.0 and linha["prazo_execucao_meses"] == 12
    assert linha["evidencias"]["teto_orcamento"]["pagina"] == 2
    assert pg.fila[1]["status"] == "ok" and pg.fila[1]["ultimo_erro"] is None
    assert pedidos == ["https://exemplo.test/e1.pdf"], "o PDF é baixado uma única vez"


def test_nao_reprocessa_documento_sem_mudanca_e_reprocessa_quando_o_hash_muda_ou_pedem():
    eds = [edital(1)]
    arqs = {"https://exemplo.test/e1.pdf": PELOTAS}
    pg, banco, web, pedidos, prov = montar(eds, arqs, resposta_pelotas(), *[VAZIA] * 6)
    rodar_tudo(pg, banco, web, prov, max_editais=20)
    chamadas_llm = len(prov.prompts)
    # 40 dias depois: a fila reconfere, baixa o PDF, vê o mesmo hash e NÃO chama o modelo
    pg.fila[1]["atualizado_em"] = (AGORA - timedelta(days=40)).isoformat()
    pg.fila[1]["status"] = "ok"
    web2, pedidos2 = web_falso(arqs)
    banco2 = Banco(ConfigSupabase("https://sb.test", "chave"), transporte=pg.transporte())
    r = rodar_tudo(pg, banco2, web2, prov, max_editais=20)
    assert r.inalterados == 1 and len(prov.prompts) == chamadas_llm and len(pedidos2) == 1
    # "Reprocessar" na tela de revisão: pendente + 'reprocessar' => roda de novo mesmo com o hash igual
    pg.fila[1].update(status="pendente", ultimo_erro="reprocessar")
    web3, _ = web_falso(arqs)
    r = rodar_tudo(
        pg,
        Banco(ConfigSupabase("https://sb.test", "chave"), transporte=pg.transporte()),
        web3,
        prov,
        max_editais=20,
    )
    assert r.ok == 1 and r.inalterados == 0 and len(prov.prompts) > chamadas_llm
    # documento novo (hash diferente): extrai de novo e os critérios revisados voltam para revisão
    pg.criterios[1]["revisado"] = True
    pg.fila[1].update(status="pendente", ultimo_erro=None)
    web4, _ = web_falso({"https://exemplo.test/e1.pdf": PREMIACAO})
    antes = len(prov.prompts)
    rodar_tudo(
        pg,
        Banco(ConfigSupabase("https://sb.test", "chave"), transporte=pg.transporte()),
        web4,
        prov,
        max_editais=20,
    )
    assert len(prov.prompts) > antes and pg.criterios[1]["revisado"] is False


def test_max_editais_por_dia_com_fila_para_os_demais():
    eds = [edital(i) for i in range(1, 6)]
    arqs = {f"https://exemplo.test/e{i}.pdf": PELOTAS for i in range(1, 6)}
    pg, banco, web, _, prov = montar(eds, arqs, *([{}] * 100))
    r = rodar_tudo(pg, banco, web, prov, max_editais=2)
    assert r.processados == 2 and r.restantes == 3
    assert sorted(pg.fila) == [1, 2, 3, 4, 5], "todos entram na fila"
    assert sorted(i for i, f in pg.fila.items() if f["status"] == "ok") == [1, 2]
    assert sorted(i for i, f in pg.fila.items() if f["status"] == "pendente") == [3, 4, 5]
    # no dia seguinte, a fila anda
    pg.fila[1]["atualizado_em"] = pg.fila[2]["atualizado_em"] = AGORA.isoformat()
    web2, _ = web_falso(arqs)
    r2 = rodar_tudo(
        pg,
        Banco(ConfigSupabase("https://sb.test", "chave"), transporte=pg.transporte()),
        web2,
        prov,
        max_editais=2,
    )
    assert r2.processados == 2 and r2.restantes == 1


def test_estados_sem_documento_e_erro_e_isolamento_entre_editais():
    eds = [
        edital(1),
        edital(2, url_edital_pdf=None, url_origem=None),
        edital(3),
        edital(4),
        edital(5),
    ]
    arqs = {
        "https://exemplo.test/e1.pdf": 403,
        "https://exemplo.test/e3.pdf": 500,
        "https://exemplo.test/e4.pdf": PELOTAS,
        "https://exemplo.test/e5.pdf": b"%PDF-1.4 lixo",
    }
    pg, banco, web, _, prov = montar(eds, arqs, resposta_pelotas(), *[VAZIA] * 6)
    r = rodar_tudo(pg, banco, web, prov, max_editais=20)
    st = {i: (f["status"], f["tentativas"]) for i, f in pg.fila.items()}
    assert (
        st[1][0] == "sem_documento" and "acesso negado" in pg.fila[1]["ultimo_erro"]
    )  # 403: não contornamos
    assert st[2][0] == "sem_documento" and "link" in pg.fila[2]["ultimo_erro"]
    assert st[3] == ("erro", 1)  # servidor com problema: tenta de novo amanhã
    assert st[4][0] == "ok" and 4 in pg.criterios, "o edital bom é processado apesar dos ruins"
    assert st[5][0] in ("erro", "sem_documento")
    assert r.processados == 5 and r.ok == 1 and r.erro >= 1 and r.sem_documento >= 2
    assert set(pg.criterios) == {4}, "nada é gravado para quem falhou"


def test_resposta_invalida_do_modelo_vira_erro_com_tentativa_e_nao_grava():
    pg, banco, web, _, prov = montar(
        [edital(1)], {"https://exemplo.test/e1.pdf": PELOTAS}, {"criterios": "lixo"}
    )
    r = rodar_tudo(pg, banco, web, prov, max_editais=20)
    assert (
        r.erro == 1
        and pg.fila[1]["status"] == "erro"
        and pg.fila[1]["tentativas"] == 1
        and pg.criterios == {}
    )


def test_sem_cota_do_modelo_interrompe_sem_gastar_tentativas():
    eds = [edital(1), edital(2)]
    arqs = {f"https://exemplo.test/e{i}.pdf": PELOTAS for i in (1, 2)}
    pg, banco, web, _, prov = montar(
        eds, arqs, ErroLlm("cota", "limite da camada gratuita do Gemini atingido")
    )
    r = rodar_tudo(pg, banco, web, prov, max_editais=20)
    assert r.interrompido and r.interrompido.startswith("cota") and r.processados == 0
    assert (
        pg.fila[1]["status"] == "pendente"
        and pg.fila[1]["tentativas"] == 0
        and pg.fila[2]["status"] == "pendente"
    )
    assert len(prov.prompts) == 1, "parou no primeiro: não insiste"


def test_sem_chave_do_gemini_so_atualiza_a_fila():
    pg, banco, web, pedidos, _ = montar([edital(1)], {"https://exemplo.test/e1.pdf": PELOTAS})
    r = rodar_tudo(pg, banco, web, None, max_editais=20)
    assert (
        pg.fila[1]["status"] == "pendente"
        and pedidos == []
        and "GEMINI_API_KEY ausente" in r.interrompido
    )


def test_dry_run_nao_grava_nada():
    pg, banco, web, _, prov = montar(
        [edital(1)], {"https://exemplo.test/e1.pdf": PELOTAS}, resposta_pelotas(), *[VAZIA] * 6
    )
    rodar_tudo(pg, banco, web, prov, max_editais=20, dry_run=True)
    assert (
        pg.fila == {}
        and pg.criterios == {}
        and not [c for c in pg.chamadas if c.startswith(("POST", "PATCH"))]
    )


def test_edital_internacional_nao_entra_na_extracao():
    eds = [edital(1, ambito="internacional"), edital(2, ambito="brasil")]
    pg, banco, web, _, prov = montar(
        eds, {"https://exemplo.test/e2.pdf": PELOTAS}, resposta_pelotas(), *[VAZIA] * 6
    )
    rodar_tudo(pg, banco, web, prov, max_editais=20)
    assert list(pg.fila) == [2]


def test_documento_sem_nada_aproveitavel_grava_linha_com_aviso_para_o_revisor():
    pg, banco, web, _, prov = montar(
        [edital(1)], {"https://exemplo.test/e1.pdf": DIVINO}, *[VAZIA] * 6
    )
    rodar_tudo(pg, banco, web, prov, max_editais=20)
    c = pg.criterios[1]
    assert (
        c["teto_orcamento"] is None
        and c["criterios"] == []
        and "anexo separado" in c["observacoes"]
        and c["revisado"] is False
    )
    assert pg.fila[1]["status"] == "ok", "não reprocessa todo dia um documento sem critérios"


# ── LLM (cliente) e linha de comando ─────────────────────────────────────────────────────────


def test_cliente_gemini_envia_esquema_json_trata_429_e_chave_recusada():
    chamadas = []

    def tratar(req: httpx.Request) -> httpx.Response:
        corpo = json.loads(req.content)
        chamadas.append(corpo)
        assert req.headers["x-goog-api-key"] == "k" and "key=" not in str(req.url), (
            "a chave vai no cabeçalho, nunca na URL"
        )
        if len(chamadas) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": '{"criterios": []}'}]}}]}
        )

    async def dormir(_):
        return None

    p = ProvedorGemini(
        "k", "gemini-teste", intervalo_s=0, transporte=httpx.MockTransport(tratar), dormir=dormir
    )
    assert asyncio.run(p.gerar("oi", {"type": "OBJECT"})) == {"criterios": []}
    cfg = chamadas[-1]["generationConfig"]
    assert (
        cfg["responseMimeType"] == "application/json"
        and cfg["responseSchema"] == {"type": "OBJECT"}
        and cfg["temperature"] == 0
    )
    recusada = ProvedorGemini(
        "k",
        intervalo_s=0,
        transporte=httpx.MockTransport(lambda r: httpx.Response(403)),
        dormir=dormir,
    )
    with pytest.raises(ErroLlm) as e:
        asyncio.run(recusada.gerar("oi", {}))
    assert e.value.tipo == "config"


def test_linha_de_comando_sem_credenciais_do_supabase_sai_com_erro(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert asyncio.run(ex.principal([])) == 2


def test_max_editais_vem_do_ambiente_com_padrao_20(monkeypatch):
    monkeypatch.delenv("MAX_EDITAIS_POR_DIA", raising=False)
    assert ex.max_editais_do_ambiente() == 20
    monkeypatch.setenv("MAX_EDITAIS_POR_DIA", "7")
    assert ex.max_editais_do_ambiente() == 7
    monkeypatch.setenv("MAX_EDITAIS_POR_DIA", "lixo")
    assert ex.max_editais_do_ambiente() == 20


def test_paginas_de_pdf_com_pypdf_quando_nao_ha_pdftotext(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    paginas = paginas_de_pdf(PREMIACAO)
    assert len(paginas) == 11 and "73.200,00" in " ".join(paginas)


# ── workflow ─────────────────────────────────────────────────────────────────────────────────


def test_workflow_diario_roda_a_extracao_em_job_proprio_sem_segredo_no_repositorio():
    import yaml

    texto = (
        Path(__file__).resolve().parent.parent / ".github" / "workflows" / "sync_editais.yml"
    ).read_text(encoding="utf-8")
    wf = yaml.safe_load(texto)
    j = wf["jobs"]["criterios"]
    assert j["needs"] == "sync" and j["if"].startswith("${{ always()"), (
        "roda depois da coleta, mesmo se ela falhar"
    )
    assert j["continue-on-error"] is True and j["timeout-minutes"] <= 30
    passo = next(p for p in j["steps"] if p.get("name", "").startswith("Extrair"))
    assert passo["env"]["GEMINI_API_KEY"] == "${{ secrets.GEMINI_API_KEY }}"
    assert passo["env"]["MAX_EDITAIS_POR_DIA"] == "${{ vars.MAX_EDITAIS_POR_DIA }}"
    assert "python -m scripts.extrair_criterios" in passo["run"]
    assert "AIza" not in texto and "service_role" not in texto.lower().replace(
        "service_role_key", ""
    )
    # a coleta não depende da extração
    assert "criterios" not in str(wf["jobs"]["sync"].get("needs", ""))
    assert (
        wf["jobs"]["sync"]["if"]
        == "${{ inputs.somente_criterios != true && inputs.somente_sombra != true }}"
    )
