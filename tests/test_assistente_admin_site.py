"""Tela /admin/criterios/ no navegador: só administradores, revisão com evidência, e o que é gravado."""

import json
import re

from test_assistente_fase2_site import BackendFase2
from test_assistente_site import USUARIO, preparar

RAIZ_ADMIN = "admin/criterios/"
DOC = "https://exemplo.invalid/edital-7.pdf"


class BackendAdmin(BackendFase2):
    def __init__(self, specs, admin=True):
        super().__init__(specs)
        self.admin = admin
        self.lista = [
            {
                "edital_id": 7,
                "titulo": "Edital Teste de Teatro 01/2026",
                "url_origem": "https://exemplo.invalid/edital-1",
                "url_edital_pdf": DOC,
                "inscricao_fim": "2026-12-20T02:59:59Z",
                "projetos": 3,
                "extraido_em": "2026-11-08T08:00:00Z",
                "fila_status": "ok",
                "fila_erro": None,
            },
            {
                "edital_id": 8,
                "titulo": "Edital Menos Usado",
                "url_origem": "https://exemplo.invalid/e8",
                "url_edital_pdf": None,
                "inscricao_fim": "2026-12-30T02:59:59Z",
                "projetos": 0,
                "extraido_em": None,
                "fila_status": "ok",
                "fila_erro": None,
            },
        ]
        self.fila_rows = [
            {
                "edital_id": 7,
                "status": "erro",
                "ultimo_erro": "download: HTTP 500",
                "tentativas": 2,
                "atualizado_em": "2026-11-08T05:00:00Z",
            }
        ]
        self.criterios[7] = {
            "edital_id": 7,
            "fonte_url": DOC,
            "fonte_hash": "abc",
            "revisado": False,
            "modelo_llm": "gemini-teste",
            "extraido_em": "2026-11-08T08:00:00Z",
            "teto_orcamento": 30000,
            "teto_por_proponente": None,
            "percentuais_max": {"divulgacao": 0.15},
            "percentuais_min": {},
            "rubricas_vedadas": ["Bebidas alcoólicas"],
            "limites_caracteres": {"metas": 2500},
            "campos_exigidos": [{"nome": "Plano de trabalho", "secao": None}],
            "contrapartida_exigida": None,
            "prazo_execucao_meses": 12,
            "exige_acessibilidade": True,
            "observacoes": None,
            "criterios": [
                {
                    "id": "merito",
                    "nome": "Mérito cultural",
                    "descricao": None,
                    "peso": None,
                    "nota_max": 10,
                    "trecho_edital": "O mérito vale até 10 pontos",
                    "pagina": 12,
                }
            ],
            "evidencias": {
                "teto_orcamento": {
                    "trecho": "valor máximo de R$ 30.000,00 por projeto",
                    "pagina": 2,
                },
                "percentuais_max.divulgacao": {"trecho": "divulgação até 15%", "pagina": 5},
            },
        }
        self.patches: list[tuple[str, dict, dict]] = []
        self.pedidos: list[str] = []

    def responder(self, metodo, caminho, q, corpo):
        tabela = caminho.rsplit("/", 1)[-1]
        self.pedidos.append(f"{metodo} {tabela}")
        if tabela == "admins":
            return 200, [{"user_id": USUARIO}] if self.admin else []
        if tabela == "admin_criterios_para_revisar":
            return 200, self.lista if self.admin else []
        if tabela == "extracao_fila":
            if metodo == "PATCH":
                self.patches.append(("extracao_fila", q, corpo))
                return 204, None
            return 200, self.fila_rows
        if tabela == "edital_criterios" and metodo == "PATCH":
            self.patches.append(("edital_criterios", q, corpo))
            self.criterios[int(q["edital_id"].removeprefix("eq."))].update(corpo)
            return 204, None
        if tabela == "editais" and q.get("id", "").startswith("eq."):
            return 200, [e for e in self.editais if str(e["id"]) == q["id"][3:]]
        return super().responder(metodo, caminho, q, corpo)


def abrir_admin(navegador, url, caminho="", logado=True, admin=True, largura=1280):
    from test_assistente_site import specs_do_site

    pg, be = preparar(navegador, specs_do_site(url), logado=logado, largura=largura)
    be.__class__ = BackendAdmin
    BackendAdmin.__init__(be, be.specs, admin=admin)
    pg.goto(url + RAIZ_ADMIN + caminho)
    pg.wait_for_selector("#admin-raiz h1")
    return pg, be


def test_sem_login_pede_para_entrar_pelo_assistente_e_nao_consulta_nada(navegador, url):
    pg, be = abrir_admin(navegador, url, logado=False)
    assert "só para administradores" in pg.locator("#admin-raiz").inner_text()
    assert (
        pg.get_by_role("link", name="Entrar pelo assistente")
        .get_attribute("href")
        .endswith("/projeto/")
    )
    assert be.pedidos == []
    pg.context.close()


def test_usuario_comum_ve_acesso_restrito_e_nenhum_dado_de_criterios_e_pedido(navegador, url):
    pg, be = abrir_admin(navegador, url, admin=False)
    assert "Acesso restrito" in pg.locator("#admin-raiz h1").inner_text()
    assert "não é administradora" in pg.locator("#admin-raiz").inner_text()
    assert be.pedidos == ["GET admins"], (
        "só pergunta se é admin; não pede critérios, fila nem lista"
    )
    # mesmo sabendo o endereço do detalhe
    pg2, be2 = abrir_admin(navegador, url, "?edital=7", admin=False)
    assert (
        "Acesso restrito" in pg2.locator("h1").inner_text()
        and "GET edital_criterios" not in be2.pedidos
    )
    pg.context.close()
    pg2.context.close()


def test_admin_ve_a_lista_na_ordem_do_servidor_e_a_aba_de_problemas(navegador, url):
    pg, be = abrir_admin(navegador, url)
    assert pg.get_by_role("heading", name="Revisão de critérios dos editais").is_visible()
    itens = pg.locator(".item-revisao")
    assert itens.count() == 2
    assert (
        "Edital Teste de Teatro" in itens.first.inner_text()
        and "3 projeto(s)" in itens.first.inner_text()
    )
    assert pg.get_by_role("tab", name="Para revisar (2)").get_attribute("aria-selected") == "true"
    pg.get_by_role("tab", name="Com problema (1)").click()
    item = pg.locator(".item-revisao")
    assert "erro · 2 tentativa(s) · download: HTTP 500" in item.inner_text()
    item.get_by_role("button", name="Reprocessar").click()
    pg.get_by_text("Na fila para a próxima extração.").wait_for()
    tabela, filtro, corpo = be.patches[-1]
    assert tabela == "extracao_fila" and filtro["edital_id"] == "eq.7"
    assert (
        corpo["status"] == "pendente"
        and corpo["tentativas"] == 0
        and corpo["ultimo_erro"] == "reprocessar"
    )
    assert pg.erros == []
    pg.context.close()


def test_detalhe_mostra_valores_editaveis_evidencia_e_link_para_a_pagina(navegador, url):
    pg, be = abrir_admin(navegador, url, "?edital=7")
    assert pg.locator("h1").inner_text() == "Edital Teste de Teatro 01/2026"
    assert "lidos automaticamente" in pg.locator(".aviso-fixo").inner_text()
    assert pg.get_by_label("Teto por projeto (R$)").input_value() == "30000"
    assert pg.get_by_label("Divulgação: máximo (%)").input_value() == "15"
    assert pg.get_by_label("Prazo de execução (meses)").input_value() == "12"
    assert pg.get_by_label("O edital exige acessibilidade?").input_value() == "sim"
    assert pg.get_by_label("Uma por linha").input_value() == "Bebidas alcoólicas"
    ev = pg.locator(".evidencia", has_text="valor máximo de R$ 30.000,00")
    assert ev.get_by_role("link").get_attribute("href") == DOC + "#page=2"
    assert "ver no documento (p. 2)" in ev.inner_text()
    assert (
        pg.locator(".evidencia", has_text="O mérito vale até 10 pontos")
        .get_by_role("link")
        .get_attribute("href")
        == DOC + "#page=12"
    )
    assert pg.get_by_role("link", name="Abrir o documento original").get_attribute("href") == DOC
    pg.context.close()


def test_editar_e_salvar_nao_marca_revisado_e_converte_percentual_para_fracao(navegador, url):
    pg, be = abrir_admin(navegador, url, "?edital=7")
    pg.get_by_label("Teto por projeto (R$)").fill("32.500,00")
    pg.get_by_label("Divulgação: máximo (%)").fill("12,5")
    pg.get_by_label("Uma por linha").fill("Bebidas alcoólicas\nBrindes")
    pg.get_by_role("button", name="Salvar alterações").click()
    pg.get_by_text("Alterações salvas.").first.wait_for()
    _, filtro, corpo = be.patches[-1]
    assert filtro["edital_id"] == "eq.7"
    assert corpo["teto_orcamento"] == 32500.0 and corpo["percentuais_max"] == {"divulgacao": 0.125}
    assert (
        corpo["rubricas_vedadas"] == ["Bebidas alcoólicas", "Brindes"]
        and corpo["exige_acessibilidade"] is True
    )
    assert "revisado" not in corpo, "salvar não é revisar"
    assert corpo["criterios"][0]["trecho_edital"] == "O mérito vale até 10 pontos"
    pg.context.close()


def test_marcar_como_revisado_grava_quem_e_quando(navegador, url):
    pg, be = abrir_admin(navegador, url, "?edital=7")
    pg.get_by_role("button", name="Marcar como revisado").click()
    pg.get_by_text("Marcado como revisado.").first.wait_for()
    corpo = be.patches[-1][2]
    assert corpo["revisado"] is True and corpo["revisado_por"] == USUARIO
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z", corpo["revisado_em"])
    assert "conferidos por uma pessoa" in pg.locator(".aviso-fixo").inner_text()
    pg.context.close()


def test_valor_invalido_mostra_erro_no_campo_e_nao_grava(navegador, url):
    pg, be = abrir_admin(navegador, url, "?edital=7")
    pg.get_by_label("Teto por projeto (R$)").fill("zero")
    pg.get_by_label("Divulgação: máximo (%)").fill("150")
    pg.get_by_role("button", name="Marcar como revisado").click()
    assert "Corrija 2 campo(s)" in pg.locator(".msg-form.erro").first.inner_text()
    assert pg.get_by_label("Teto por projeto (R$)").get_attribute("aria-invalid") == "true"
    assert "Digite um número." in pg.locator("main").inner_text()
    assert be.patches == []
    pg.context.close()


def test_sem_criterios_aproveitaveis_pede_confirmacao_e_zera_tudo_como_revisado(navegador, url):
    pg, be = abrir_admin(navegador, url, "?edital=7")
    pg.get_by_role("button", name="Sem critérios aproveitáveis").click()
    assert be.patches == [], "o primeiro clique só pede confirmação"
    pg.get_by_role("button", name="Confirmar: apagar o que foi lido").click()
    pg.get_by_text("Marcado como sem critérios aproveitáveis.").first.wait_for()
    corpo = be.patches[-1][2]
    assert (
        corpo["criterios"] == []
        and corpo["teto_orcamento"] is None
        and corpo["percentuais_max"] == {}
        and corpo["revisado"] is True
    )
    assert (
        corpo["revisado_por"] == USUARIO
        and "não traz critérios aproveitáveis" in corpo["observacoes"]
    )
    assert pg.get_by_label("Teto por projeto (R$)").input_value() == ""
    pg.context.close()


def test_reprocessar_no_detalhe_vai_para_a_fila_sem_perder_as_edicoes(navegador, url):
    pg, be = abrir_admin(navegador, url, "?edital=7")
    pg.get_by_label("Prazo de execução (meses)").fill("18")
    pg.get_by_role("button", name="Reprocessar").click()
    pg.get_by_text("Na fila:").wait_for()
    assert (
        be.patches[-1][0] == "extracao_fila" and be.patches[-1][2]["ultimo_erro"] == "reprocessar"
    )
    assert pg.get_by_label("Prazo de execução (meses)").input_value() == "18"
    assert all(t != "edital_criterios" for t, _, _ in be.patches), "reprocessar não grava critérios"
    pg.context.close()


def test_campos_exigidos_e_criterios_podem_ser_adicionados_e_removidos(navegador, url):
    pg, be = abrir_admin(navegador, url, "?edital=7")
    pg.get_by_role("button", name="Adicionar campo").click()
    novo = pg.locator(".lista-campos").first.locator("li").nth(1)
    novo.get_by_label("Nome do campo").fill("Plano de acessibilidade")
    novo.get_by_label("Seção do assistente").select_option("acessibilidade")
    pg.get_by_role("button", name="Remover o critério Mérito cultural").click()
    pg.get_by_role("button", name="Salvar alterações").click()
    pg.get_by_text("Alterações salvas.").first.wait_for()
    corpo = be.patches[-1][2]
    assert corpo["campos_exigidos"] == [
        {"nome": "Plano de trabalho", "secao": None},
        {"nome": "Plano de acessibilidade", "secao": "acessibilidade"},
    ]
    assert corpo["criterios"] == []
    pg.context.close()


def test_pagina_admin_e_acessivel_cabe_no_celular_e_nao_e_indexada(navegador, url):
    pg, be = abrir_admin(navegador, url, "?edital=7", largura=360)
    assert pg.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    campos = pg.locator("main input:visible, main select:visible, main textarea:visible")
    for i in range(campos.count()):
        nome = pg.evaluate(
            "(el) => (el.labels && el.labels[0] ? el.labels[0].textContent : el.getAttribute('aria-label')) || ''",
            campos.nth(i).element_handle(),
        )
        assert nome.strip(), f"campo {i} sem rótulo"
    assert pg.locator('meta[name="robots"]').get_attribute("content") == "noindex, nofollow"
    robots = json.dumps(open("web/robots.txt", encoding="utf-8").read())
    assert "Disallow: /admin/" in robots
    pg.context.close()
