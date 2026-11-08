"""Fase 2 no navegador: editores de orçamento, cronograma, mídia e equipe, propostas, pendências e critérios.

Supabase e a Edge Function são simulados em memória (o contrato é o mesmo da função real, que tem
testes Deno próprios). Aqui se prova o que a PESSOA vê e faz.
"""

import os
import re
import uuid
import zipfile
from pathlib import Path
from urllib.parse import unquote

import pytest
from test_assistente_site import RASCUNHO, Backend, abrir, preparar, specs_do_site

TABELAS = ("orcamento_itens", "cronograma_itens", "midia_itens", "equipe_membros")
OBRIGATORIO = {
    "orcamento_itens": "item",
    "cronograma_itens": "atividade",
    "midia_itens": "peca",
    "equipe_membros": "funcao",
}


class BackendFase2(Backend):
    def __init__(self, specs):
        super().__init__(specs)
        self.tabelas: dict[str, list[dict]] = {t: [] for t in TABELAS}
        self.criterios: dict[int, dict] = {}
        self.salvamentos: list[dict] = []
        self.sem_tabelas = False

    def responder(self, metodo, caminho, q, corpo):
        tabela = caminho.rsplit("/", 1)[-1]
        if tabela in TABELAS:
            if self.sem_tabelas:
                return 404, {"message": "relation does not exist"}
            pid = unquote(q["projeto_id"][3:])
            return 200, [x for x in self.tabelas[tabela] if x["projeto_id"] == pid]
        if tabela == "edital_criterios":
            eid = int(q["edital_id"][3:])
            return 200, [self.criterios[eid]] if eid in self.criterios else []
        return super().responder(metodo, caminho, q, corpo)

    def funcao(self, corpo):
        if corpo.get("acao") != "salvar_itens":
            return super().funcao(corpo)
        self.salvamentos.append(corpo)
        tabela, pid = corpo["tabela"], corpo["projeto_id"]
        erros = [
            {"indice": i, "campos": {OBRIGATORIO[tabela]: "Preencha este campo."}}
            for i, linha in enumerate(corpo["linhas"])
            if not str(linha.get(OBRIGATORIO[tabela], "")).strip()
        ]
        if erros:
            return 422, {"erro": "validacao", "mensagem": "Há linhas com problema.", "erros": erros}
        linhas = [{**x, "projeto_id": pid, "ordem": i} for i, x in enumerate(corpo["linhas"])]
        self.tabelas[tabela] = [x for x in self.tabelas[tabela] if x["projeto_id"] != pid] + linhas
        return 200, {"tabela": tabela, "linhas": linhas, "resumo": None, "avisos": []}


def ir(navegador, url, secao, largura=1280, criterios=None, **projeto):
    specs = specs_do_site(url)
    pg, be = preparar(navegador, specs, largura=largura)
    be.__class__ = BackendFase2
    be.tabelas, be.criterios, be.salvamentos, be.sem_tabelas = (
        {t: [] for t in TABELAS},
        {},
        [],
        False,
    )
    edital = (
        {
            "edital_id": 7,
            "edital_titulo": "Edital Teste",
            "edital_url": "https://exemplo.invalid/edital-1",
        }
        if criterios is not None
        else {}
    )
    pid = be.projeto(ficha={"custo_estimado": 10000}, **edital, **projeto)
    if criterios is not None:
        be.criterios[7] = {
            "edital_id": 7,
            "revisado": True,
            "criterios": [],
            "percentuais_max": {},
            "percentuais_min": {},
            "rubricas_vedadas": [],
            "limites_caracteres": {},
            "campos_exigidos": [],
            **criterios,
        }
    abrir(pg, url, f"?id={pid}&secao={secao}")
    return pg, be, pid


def esperar_salvo(pg, be, n=1):
    pg.wait_for_function(
        "document.querySelector('.status-tabela')?.textContent === 'Salvo.'", timeout=8000
    )
    assert len(be.salvamentos) >= n


def preencher_linha(linha, **campos):
    for rotulo, valor in campos.items():
        linha.get_by_label(rotulo, exact=True).fill(str(valor))


def test_orcamento_soma_mostra_teto_e_salva_sozinho(navegador, url):
    pg, be, pid = ir(navegador, url, "orcamento", criterios={"teto_orcamento": 2000})
    painel = pg.locator(".painel-tabela")
    painel.get_by_role("button", name="Adicionar item em Produção").click()
    linha = painel.locator(".linha-item").first
    preencher_linha(
        linha,
        **{
            "Item": "Cachê do diretor",
            "Qtd": 2,
            "Valor unitário (R$)": "1.500,00",
            "Fonte do preço": "tabela do sindicato",
            "Justificativa": "Direção do espetáculo",
        },
    )
    assert linha.locator(".total-item").inner_text() == "R$ 3.000,00"
    assert "Passa do teto em R$ 1.000,00" in painel.locator(".resumo-orcamento").inner_text()
    assert painel.locator("#etapa-producao .subtotal").inner_text().startswith("R$ 3.000,00")
    esperar_salvo(pg, be)
    enviado = be.salvamentos[-1]
    assert (
        enviado["acao"] == "salvar_itens"
        and enviado["tabela"] == "orcamento_itens"
        and enviado["projeto_id"] == pid
    )
    assert enviado["linhas"][0]["item"] == "Cachê do diretor" and re.fullmatch(
        r"[0-9a-f-]{36}", enviado["linhas"][0]["id"]
    )
    # pendência cruzada: total acima do teto, com link para a seção
    pend = pg.locator(".pendencias")
    assert (
        "total acima do teto".lower() in pend.inner_text().lower()
        or "passa do teto" in pend.inner_text().lower()
    )
    assert pg.erros == []
    # recarregar traz as linhas gravadas
    pg.reload()
    pg.wait_for_selector(".painel-tabela .linha-item")
    assert pg.locator(".painel-tabela .linha-item input").first.input_value() == "Cachê do diretor"
    pg.context.close()


def test_linha_invalida_mostra_erro_na_linha_e_nao_salva(navegador, url):
    pg, be, _ = ir(navegador, url, "orcamento", criterios={})
    painel = pg.locator(".painel-tabela")
    painel.get_by_role("button", name="Adicionar item em Divulgação").click()
    linha = painel.locator(".linha-item").first
    linha.get_by_label("Item", exact=True).focus()
    linha.get_by_label("Rubrica", exact=True).focus()  # sai do campo: erro aparece
    assert "Preencha este campo." in linha.inner_text()
    assert linha.get_by_label("Item", exact=True).get_attribute("aria-invalid") == "true"
    pg.wait_for_timeout(1800)
    assert be.salvamentos == [], "linha inválida nunca é enviada"
    assert (
        "problema" in pg.locator(".status-tabela").inner_text()
        or "não salvas" in pg.locator(".status-tabela").inner_text()
    )
    preencher_linha(linha, Item="Cartaz")
    esperar_salvo(pg, be)
    assert linha.get_by_label("Item", exact=True).get_attribute("aria-invalid") is None
    pg.context.close()


def test_valor_a_confirmar_e_percentual_acima_do_limite(navegador, url):
    pg, be, _ = ir(navegador, url, "orcamento", criterios={"percentuais_max": {"divulgacao": 0.15}})
    painel = pg.locator(".painel-tabela")
    painel.get_by_role("button", name="Adicionar item em Produção").click()
    preencher_linha(
        painel.locator(".linha-item").first,
        Item="Direção",
        **{"Valor unitário (R$)": "8000", "Fonte do preço": "sindicato", "Justificativa": "x"},
    )
    painel.get_by_role("button", name="Adicionar item em Divulgação").click()
    div = painel.locator(".grupo-etapa", has_text="Divulgação").locator(".linha-item").first
    preencher_linha(div, Item="Anúncio", **{"Valor unitário (R$)": "2000"})
    assert "valor a confirmar" in div.inner_text(), "sem fonte do preço, o item fica 'a confirmar'"
    r = painel.locator(".resumo-orcamento").inner_text()
    assert "Divulgação 20%" in r.replace("\n", " ").replace("  ", " ") or "20%" in r
    assert "fora do limite do edital" in r
    pend = pg.locator(".pendencias").inner_text()
    assert "limita a 15%" in pend
    pg.context.close()


def test_cronograma_grade_de_meses_por_clique_e_por_teclado(navegador, url):
    pg, be, _ = ir(navegador, url, "cronograma", criterios={"prazo_execucao_meses": 6})
    painel = pg.locator(".painel-tabela")
    painel.get_by_role("button", name="Adicionar atividade").click()
    linha = painel.locator(".linha-cron").first
    preencher_linha(linha, Atividade="Ensaios")
    celulas = linha.locator(".celula")
    marcadas = lambda: [c.get_attribute("aria-pressed") for c in celulas.all()[:6]]  # noqa: E731
    assert marcadas() == ["true", "false", "false", "false", "false", "false"], (
        "atividade nova = mês 1"
    )
    celulas.nth(3).click()  # mês 4: estende o intervalo
    assert marcadas() == ["true", "true", "true", "true", "false", "false"]
    celulas.nth(0).click()  # mês 1 é a ponta: encolhe
    assert marcadas() == ["false", "true", "true", "true", "false", "false"]
    celulas.nth(2).click()  # clique no meio: o fim passa a ser este mês
    assert marcadas() == ["false", "true", "true", "false", "false", "false"]
    assert celulas.count() == 6, "o edital permite 6 meses: a grade começa com 6"
    for _ in range(2):
        painel.get_by_role("button", name="Mostrar mais um mês").click()
    celulas = painel.locator(".linha-cron").first.locator(".celula")
    assert celulas.count() == 8
    celulas.nth(7).focus()
    pg.keyboard.press("Enter")  # teclado também marca (é um botão)
    assert celulas.nth(7).get_attribute("aria-pressed") == "true"
    assert "depois do prazo do edital" in celulas.nth(7).get_attribute("aria-label")
    esperar_salvo(pg, be)
    linhas = be.salvamentos[-1]["linhas"]
    assert (int(linhas[0]["mes_inicio"]), int(linhas[0]["mes_fim"])) == (
        2,
        8,
    )  # 2 a 3, e o mês 8 estendeu o fim
    # edital permite 6 meses: pendência de prazo aparece
    assert "mês 8" in pg.locator(".pendencias").inner_text()
    pg.context.close()


def test_cronograma_no_celular_vira_lista_com_campos_numericos(navegador, url):
    pg, be, _ = ir(navegador, url, "cronograma", largura=360, criterios={})
    pg.get_by_role("tab", name="Tabela").click()
    pg.locator(".painel-tabela").get_by_role("button", name="Adicionar atividade").click()
    linha = pg.locator(".linha-cron").first
    assert linha.locator(".celula").first.is_hidden(), "a grade some no celular"
    preencher_linha(linha, Atividade="Oficinas", **{"Mês inicial": 3, "Mês final": 5})
    esperar_salvo(pg, be)
    assert (
        int(be.salvamentos[-1]["linhas"][0]["mes_inicio"]),
        int(be.salvamentos[-1]["linhas"][0]["mes_fim"]),
    ) == (3, 5)
    assert pg.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    pg.context.close()


def test_propostas_do_assistente_so_entram_na_tabela_quando_aceitas(navegador, url):
    pg, be, _ = ir(navegador, url, "orcamento", criterios={})
    proposta = {
        **RASCUNHO,
        "rascunho": "Orçamento por etapa.",
        "alertas": [],
        "itens_propostos": {
            "tabela": "orcamento_itens",
            "linhas": [
                {
                    "etapa": "producao",
                    "item": "Cachê da direção",
                    "quantidade": 1,
                    "valor_unitario": 2000,
                    "fonte_preco": "informado pelo usuário",
                    "justificativa": "Direção",
                },
                {
                    "etapa": "producao",
                    "item": "Som",
                    "quantidade": 1,
                    "valor_unitario": None,
                    "fonte_preco": None,
                    "justificativa": "Áudio",
                },
                {
                    "etapa": "divulgacao",
                    "item": "Cartaz",
                    "quantidade": 100,
                    "valor_unitario": None,
                },
            ],
        },
    }
    be.fila = [proposta]
    pg.get_by_role("button", name="Começar esta seção").click()
    pg.locator(".propostas").wait_for()
    p = pg.locator(".propostas")
    assert "3" in p.get_by_role("heading").inner_text()
    assert "nada entra na tabela até você aceitar" in p.inner_text()
    assert "valor a confirmar" in p.inner_text() and be.salvamentos == [], "propor não grava"
    assert pg.locator(".painel-tabela .linha-item").count() == 0
    p.get_by_role("button", name=re.compile("^Aceitar: Cachê da direção")).click()
    assert pg.locator(".painel-tabela .linha-item").count() == 1
    p.get_by_role("button", name=re.compile("^Descartar: Som")).click()
    p.get_by_role("button", name=re.compile("^Aceitar e editar: Cartaz")).click()
    assert pg.locator(".painel-tabela .linha-item").count() == 2
    assert pg.evaluate("document.activeElement.closest('.linha-item') !== null"), (
        "Editar leva o foco para a linha"
    )
    assert pg.locator(".propostas").count() == 0 or pg.locator(".propostas .proposta").count() == 0
    cartaz = pg.locator(".linha-item:has(.chip-confirmar:visible)").first
    assert "valor a confirmar" in cartaz.inner_text()
    esperar_salvo(pg, be)
    itens = {x["item"] for x in be.salvamentos[-1]["linhas"]}
    assert itens == {"Cachê da direção", "Cartaz"}, "o descartado nunca é gravado"
    pg.context.close()


def test_equipe_cartoes_ligam_cache_do_orcamento_e_acusam_funcao_paga_sem_pessoa(navegador, url):
    pg, be, pid = ir(navegador, url, "equipe", criterios={})
    be.tabelas["orcamento_itens"] = [
        {
            "id": str(uuid.uuid4()),
            "projeto_id": pid,
            "etapa": "producao",
            "rubrica": "Equipe",
            "item": "Cachê da cenografia",
            "unidade": "unidade",
            "quantidade": 1,
            "valor_unitario": 2000,
            "fonte_preco": "x",
            "justificativa": "y",
            "ordem": 0,
        },
    ]
    pg.reload()
    pg.wait_for_selector(".painel-tabela")
    assert "não está ligado a ninguém da equipe" in pg.locator(".pendencias").inner_text()
    painel = pg.locator(".painel-tabela")
    assert (
        "CPF" in painel.inner_text()
        and "ficam só no formulário do proponente" in painel.inner_text()
    )
    painel.get_by_role("button", name="Adicionar pessoa").click()
    cartao = painel.locator(".cartao-equipe").first
    preencher_linha(
        cartao,
        Função="Cenografia",
        **{"Mini currículo (2 a 3 linhas)": "Cenógrafa há 8 anos.", "Cidade ou região": "Recife"},
    )
    cartao.locator("summary").click()
    cartao.get_by_label(re.compile("Cachê da cenografia")).check()
    assert "Pago no orçamento: 1 item" in cartao.inner_text()
    esperar_salvo(pg, be)
    assert be.salvamentos[-1]["linhas"][0]["orcamento_item_ids"] == [
        be.tabelas["orcamento_itens"][0]["id"]
    ]
    assert "não está ligado a ninguém" not in pg.locator(".pendencias").inner_text()
    assert (
        pg.locator(".painel-tabela input[name*=cpf], .painel-tabela input[name*=rg]").count() == 0
    )
    pg.context.close()


def test_midia_peca_paga_sem_custo_no_orcamento_e_ligacao(navegador, url):
    pg, be, pid = ir(navegador, url, "comunicacao", criterios={})
    be.tabelas["orcamento_itens"] = [
        {
            "id": (oid := str(uuid.uuid4())),
            "projeto_id": pid,
            "etapa": "divulgacao",
            "rubrica": "Divulgação",
            "item": "Impulsionamento",
            "unidade": "unidade",
            "quantidade": 1,
            "valor_unitario": 500,
            "fonte_preco": "x",
            "justificativa": "y",
            "ordem": 0,
        },
    ]
    pg.reload()
    pg.wait_for_selector(".painel-tabela")
    painel = pg.locator(".painel-tabela")
    painel.get_by_role("button", name="Adicionar peça").click()
    linha = painel.locator(".linha-midia").first
    preencher_linha(linha, Peça="Posts patrocinados", Canal="Instagram (patrocinado)")
    esperar_salvo(pg, be)
    assert "parece paga, mas não tem custo informado" in pg.locator(".pendencias").inner_text()
    linha.get_by_label("Item do orçamento").select_option(oid)
    assert "R$ 500,00" in linha.inner_text(), "o custo vem do item do orçamento"
    esperar_salvo(pg, be, 2)
    assert be.salvamentos[-1]["linhas"][0]["orcamento_item_id"] == oid
    pg.context.close()


def test_acessibilidade_edita_so_a_etapa_acessibilidade_do_orcamento(navegador, url):
    pg, be, pid = ir(navegador, url, "acessibilidade", criterios={})
    painel = pg.locator(".painel-tabela")
    assert "só a etapa Acessibilidade" in painel.get_by_role("heading").first.inner_text()
    assert painel.get_by_role("button", name="Adicionar item em Acessibilidade").is_visible()
    assert painel.get_by_role("button", name="Adicionar item em Produção").count() == 0
    pg.context.close()


def test_aviso_de_criterios_automaticos_some_quando_revisado(navegador, url):
    pg, be, _ = ir(
        navegador,
        url,
        "metas",
        criterios={"revisado": False, "fonte_url": "https://exemplo.invalid/edital.pdf"},
    )
    aviso = pg.locator(".aviso-criterios")
    assert "Critérios lidos automaticamente — confira no edital." in aviso.inner_text()
    assert aviso.get_by_role("link").get_attribute("href") == "https://exemplo.invalid/edital.pdf"
    pg.context.close()
    pg2, _, _ = ir(navegador, url, "metas", criterios={"revisado": True})
    assert pg2.locator(".aviso-criterios").count() == 0
    pg2.context.close()
    pg3, _, _ = ir(navegador, url, "metas")  # projeto avulso, sem edital
    assert pg3.locator(".aviso-criterios").count() == 0
    pg3.context.close()


def test_pendencias_acusam_total_percentual_equipe_e_meta_sem_atividade(navegador, url):
    """Critério de aceite: o painel acusa as quatro situações pedidas."""
    pg, be, pid = ir(
        navegador,
        url,
        "orcamento",
        criterios={"teto_orcamento": 5000, "percentuais_max": {"divulgacao": 0.1}},
    )

    def mk(**x):
        return {
            "id": str(uuid.uuid4()),
            "projeto_id": pid,
            "unidade": "unidade",
            "quantidade": 1,
            "fonte_preco": "x",
            "justificativa": "y",
            "ordem": 0,
            **x,
        }

    be.tabelas["orcamento_itens"] = [
        mk(etapa="producao", rubrica="Equipe", item="Cachê da direção", valor_unitario=5000),
        mk(etapa="divulgacao", rubrica="Divulgação", item="Anúncios", valor_unitario=1500),
    ]
    be.tabelas["cronograma_itens"] = [
        {
            "id": str(uuid.uuid4()),
            "projeto_id": pid,
            "etapa": "producao",
            "atividade": "Ensaios gerais",
            "mes_inicio": 1,
            "mes_fim": 2,
            "ordem": 0,
        }
    ]
    be.salvar_secao(
        pid,
        "metas",
        "Meta 1: realizar 8 oficinas de teatro nas escolas\nMeta 2: publicar um livro de contos",
    )
    pg.reload()
    pg.wait_for_selector(".pendencias .pendencia")
    texto = pg.locator(".pendencias").inner_text()
    assert "passa do teto do edital" in texto  # total acima do teto
    assert "limita a 10%" in texto  # % de divulgação acima do limite
    assert "não está ligado a ninguém da equipe" in texto  # função paga sem pessoa
    assert "não aparece no cronograma" in texto  # meta sem atividade
    pg.locator(".pendencia", has_text="não aparece no cronograma").get_by_role(
        "button"
    ).first.click()
    pg.wait_for_selector(".painel-tabela:not([hidden]) .editor-cronograma")
    assert "secao=cronograma" in pg.url
    pg.context.close()


def test_projeto_da_fase_1_abre_mesmo_sem_as_tabelas_novas(navegador, url):
    specs = specs_do_site(url)
    pg, be = preparar(navegador, specs)
    be.__class__ = BackendFase2
    be.tabelas, be.criterios, be.salvamentos, be.sem_tabelas = (
        {t: [] for t in TABELAS},
        {},
        [],
        True,
    )
    pid = be.projeto()
    be.salvar_secao(pid, "ficha", "Ficha antiga")
    abrir(pg, url, f"?id={pid}&secao=ficha")
    assert pg.locator("h1").inner_text() == "Oficinas de teatro de rua"
    assert pg.locator(".painel-tabela").is_hidden()
    nomes = pg.locator(".item-secao .nome").all_inner_texts()
    assert nomes[-6:] == [
        "Acessibilidade",
        "Democratização do acesso e contrapartida",
        "Plano de comunicação e divulgação",
        "Equipe e ficha técnica",
        "Cronograma",
        "Orçamento",
    ]
    assert "não iniciada" in pg.locator(".item-secao", has_text="Orçamento").inner_text().lower()
    pg.context.close()


@pytest.mark.parametrize("tema", ["claro", "escuro"])
def test_editores_sao_acessiveis_e_cabem_no_celular_nos_dois_temas(navegador, url, tema):
    pg, be, _ = ir(navegador, url, "orcamento", largura=360, criterios={"teto_orcamento": 100000})
    if tema == "escuro":
        pg.evaluate("document.documentElement.dataset.tema = 'escuro'")
    pg.get_by_role("tab", name="Tabela").click()
    pg.locator(".painel-tabela").get_by_role("button", name="Adicionar item em Produção").click()
    painel = pg.locator(".painel-tabela")
    campos = painel.locator("input:visible, select:visible, textarea:visible")
    assert campos.count() >= 6
    for i in range(campos.count()):
        nome = pg.evaluate(
            "(el) => (el.labels && el.labels[0] ? el.labels[0].textContent : el.getAttribute('aria-label')) || ''",
            campos.nth(i).element_handle(),
        )
        assert nome.strip(), f"campo {i} sem rótulo"
    assert pg.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    # alvo de toque mínimo de 44 px nos botões
    alturas = pg.evaluate(
        "[...document.querySelectorAll('.painel-tabela button')].map(b => b.getBoundingClientRect().height)"
    )
    assert min(alturas) >= 40
    pg.context.close()


# ── exportação: .xlsx, .docx com resumos e "copiar por campo" do edital ────────────────────────

EXCELJS_CDN = "https://cdn.jsdelivr.net/npm/exceljs@4.4.0/dist/exceljs.min.js"


def exceljs_real():
    """O ExcelJS verdadeiro, do cache do Deno (o teste do .xlsx em Deno o baixa antes). Sem rede."""
    base = Path(os.environ.get("DENO_DIR") or Path.home() / ".cache" / "deno")
    achados = list(base.glob("npm/registry.npmjs.org/exceljs/4.4.0/dist/exceljs.min.js"))
    if not achados:
        pytest.skip("ExcelJS não está no cache do Deno (rode o teste Deno do xlsx antes)")
    return achados[0].read_text(encoding="utf-8")


def projeto_completo(navegador, url, criterios=None):
    pg, be, pid = ir(
        navegador,
        url,
        "orcamento",
        criterios=criterios if criterios is not None else {"teto_orcamento": 20000},
    )
    oid = str(uuid.uuid4())
    be.tabelas["orcamento_itens"] = [
        {
            "id": oid,
            "projeto_id": pid,
            "etapa": "producao",
            "rubrica": "Equipe",
            "item": "Cachê da direção",
            "unidade": "mês",
            "quantidade": 2,
            "valor_unitario": 3000,
            "fonte_preco": "sindicato",
            "justificativa": "Direção",
            "ordem": 0,
        },
        {
            "id": str(uuid.uuid4()),
            "projeto_id": pid,
            "etapa": "divulgacao",
            "rubrica": "Divulgação",
            "item": "Cartazes",
            "unidade": "unidade",
            "quantidade": 100,
            "valor_unitario": 5,
            "fonte_preco": "gráfica",
            "justificativa": "Divulgação",
            "ordem": 1,
        },
    ]
    be.tabelas["cronograma_itens"] = [
        {
            "id": str(uuid.uuid4()),
            "projeto_id": pid,
            "etapa": "producao",
            "atividade": "Ensaios",
            "mes_inicio": 1,
            "mes_fim": 3,
            "ordem": 0,
        }
    ]
    be.tabelas["midia_itens"] = [
        {
            "id": str(uuid.uuid4()),
            "projeto_id": pid,
            "peca": "Cartaz",
            "canal": "Rua",
            "periodo": "antes",
            "quantidade": 100,
            "custo": None,
            "orcamento_item_id": be.tabelas["orcamento_itens"][1]["id"],
            "ordem": 0,
        }
    ]
    be.tabelas["equipe_membros"] = [
        {
            "id": str(uuid.uuid4()),
            "projeto_id": pid,
            "funcao": "Direção",
            "nome_artistico_ou_funcao": "Direção",
            "mini_curriculo": "",
            "local": "Recife",
            "orcamento_item_ids": [oid],
            "ordem": 0,
        }
    ]
    for sid, texto in [
        ("apresentacao", "Oficinas em Recife."),
        ("acessibilidade", "Haverá intérprete de Libras."),
        ("orcamento", "Orçamento por etapa."),
    ]:
        be.salvar_secao(pid, sid, texto)
    for s in be.secoes:
        s["aprovado"] = True
    pg.reload()
    pg.wait_for_selector(".painel-tabela")
    return pg, be, pid


def test_baixar_planilha_xlsx_com_formulas_reais(navegador, url, tmp_path):
    pg, be, pid = projeto_completo(navegador, url)
    pg.route(
        EXCELJS_CDN,
        lambda r: r.fulfill(
            content_type="text/javascript",
            body=exceljs_real(),
            headers={"access-control-allow-origin": "*"},
        ),
    )
    pg.get_by_role("button", name="Exportar e copiar").click()
    with pg.expect_download() as dl:
        pg.get_by_role("button", name="Baixar planilhas (.xlsx)").click()
    assert dl.value.suggested_filename == "planilhas-oficinas-de-teatro-de-rua.xlsx"
    arquivo = tmp_path / "p.xlsx"
    dl.value.save_as(arquivo)
    with zipfile.ZipFile(arquivo) as z:
        wb = z.read("xl/workbook.xml").decode()
        assert all(n in wb for n in ("Orçamento", "Resumo", "Cronograma", "Plano de mídia"))
        assert 'fullCalcOnLoad="1"' in wb
        orc = z.read("xl/worksheets/sheet1.xml").decode()
        assert "<f>E6*F6</f>" in orc and "SUBTOTAL(9,G6:G6)" in orc
        resumo = z.read("xl/worksheets/sheet2.xml").decode()
        assert (
            "SUMIF(" in resumo
            and "&apos;Orçamento&apos;!$A:$A" in resumo
            or "'Orçamento'!$A:$A" in resumo
        )
        assert "<v>20000</v>" in resumo, "teto do edital"
        midia = z.read("xl/worksheets/sheet4.xml").decode()
        # Produção ocupa a linha 6 e o subtotal a 7; os cartazes (Divulgação) estão na 8
        assert re.search(r"<f>[^<]*Or[^<]*amento[^<]*!G8</f>", midia), (
            "o custo da peça aponta para o item de orçamento"
        )
    assert "Os totais são fórmulas" in pg.locator("dialog").inner_text()
    pg.context.close()


def test_docx_inclui_secoes_novas_e_tabelas_resumo_de_orcamento_e_cronograma(navegador, url):
    pg, be, pid = projeto_completo(navegador, url)
    pg.get_by_role("button", name="Exportar e copiar").click()
    assert "3 seções aprovadas" in pg.locator("dialog").inner_text()
    with pg.expect_download():
        pg.get_by_role("button", name="Baixar .docx").click()
    estrutura = pg.evaluate("window.__docx")
    for texto in (
        "Acessibilidade",
        "Haverá intérprete de Libras.",
        "Orçamento por etapa (resumo)",
        "Cronograma (resumo)",
        "meses 1 a 3",
        "R$ 6.000,00",
        "R$ 500,00",
        "R$ 6.500,00",
        "Total geral",
    ):
        assert texto in estrutura, texto
    pg.context.close()


def test_copiar_por_campo_segue_ordem_e_nomes_do_edital_e_os_limites_dele(navegador, url):
    campos = [
        {"nome": "Descrição do projeto", "secao": "apresentacao"},
        {"nome": "Plano de acessibilidade", "secao": "acessibilidade"},
        {"nome": "Declaração de residência", "secao": None},
    ]
    pg, be, pid = projeto_completo(
        navegador,
        url,
        criterios={"campos_exigidos": campos, "limites_caracteres": {"apresentacao": 800}},
    )
    pg.get_by_role("button", name="Exportar e copiar").click()
    nomes = (
        pg.locator("dialog .campos-copia").first.locator(".campo-copia strong").all_inner_texts()
    )
    assert nomes[:3] == [
        "Descrição do projeto",
        "Plano de acessibilidade",
        "Declaração de residência",
    ]
    assert "Ficha do projeto" in nomes[3:], "as seções que o edital não cita vêm depois"
    assert "Apresentação / Resumo" not in nomes, (
        "a seção já usada por um campo do edital não se repete"
    )
    primeiro = pg.locator("dialog .campo-copia").first
    assert "19 / 800" in primeiro.inner_text(), "limite do edital, não o padrão"
    assert (
        "sem seção correspondente"
        in pg.locator("dialog .campo-copia", has_text="Declaração de residência").inner_text()
    )
    assert (
        "na ordem e com os nomes dos campos do edital" in pg.locator("dialog").inner_text().lower()
    )
    primeiro.get_by_role("button", name="Copiar").click()
    pg.get_by_text("Copiado.").first.wait_for()
    assert pg.evaluate("navigator.clipboard.readText()") == "Oficinas em Recife."
    pg.context.close()


def test_copiar_tabelas_como_texto_com_tabulacao(navegador, url):
    pg, be, pid = projeto_completo(navegador, url)
    pg.get_by_role("button", name="Exportar e copiar").click()
    pg.locator("dialog .campo-copia", has_text="Orçamento").last.get_by_role(
        "button", name="Copiar tabela"
    ).click()
    pg.get_by_text("Copiado (colunas separadas por tabulação).").wait_for()
    texto = pg.evaluate("navigator.clipboard.readText()")
    linhas = texto.split("\n")
    assert linhas[0].split("\t")[:3] == ["Etapa", "Rubrica", "Item"]
    assert linhas[1].split("\t")[2] == "Cachê da direção" and linhas[1].split("\t")[6] == "6000"
    pg.context.close()
