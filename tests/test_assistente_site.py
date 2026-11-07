"""Assistente de projetos no navegador (Playwright), com Supabase, a função e o CDN do docx simulados."""

import base64
import json
import re
import time
import uuid
from urllib.parse import parse_qs, unquote, urlparse

from e2e_util import nova_pagina

SB = "https://supabase.test"
DOCX_CDN = "https://cdn.jsdelivr.net/npm/docx@9.5.0/+esm"
CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "*",
    "access-control-allow-methods": "*",
}
DOCX_FALSO = """
class Nó { constructor(o) { this.o = o; } }
export class Paragraph extends Nó {} export class TextRun extends Nó {}
export class PageBreak extends Nó {} export class Document extends Nó {}
export const HeadingLevel = { TITLE: 'TITLE', HEADING_1: 'H1' };
export const AlignmentType = { CENTER: 'CENTER' };
export const Packer = { toBlob: async (d) => { window.__docx = JSON.stringify(d);
  return new Blob(['docx'], { type: 'application/octet-stream' }); } };
"""
USUARIO = str(uuid.uuid4())


def jwt(email="ana@exemplo.invalid", exp=None):
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{b({'alg': 'HS256'})}.{b({'sub': USUARIO, 'email': email, 'exp': exp or int(time.time()) + 3600})}.sig"


class Backend:
    """Supabase de mentira, em memória: o suficiente para o front conversar."""

    def __init__(self, specs):
        self.specs = specs
        self.projetos: dict[str, dict] = {}
        self.secoes: list[dict] = []
        self.mensagens: list[dict] = []
        self.hist: list[dict] = []
        self.proponente: dict[str, dict] = {}
        self.editais = [
            {"id": 7, "chave": "mapas:ce:1", "titulo": "Edital Teste de Teatro 01/2026", "orgao_fomento": "Secult CE",
             "url_origem": "https://exemplo.invalid/edital-1", "num_cotas": 2, "teto_por_projeto": 80000},
        ]  # fmt: skip
        self.fila: list[dict | tuple] = []  # respostas da função, na ordem
        self.chamadas: list[dict] = []
        self.requisicoes: list[str] = []

    def projeto(self, **extra):
        pid = str(uuid.uuid4())
        self.projetos[pid] = {
            "id": pid, "user_id": USUARIO, "titulo": "Oficinas de teatro de rua", "fluxo": "padrao",
            "ficha": {}, "edital_id": None, "edital_chave": None, "edital_titulo": None, "edital_url": None,
            "updated_at": "2026-11-07T12:00:00Z", **extra,
        }  # fmt: skip
        return pid

    # ---- roteamento
    def tratar(self, route):
        req = route.request
        u = urlparse(req.url)
        if req.method == "OPTIONS":
            return route.fulfill(status=204, headers=CORS)
        self.requisicoes.append(f"{req.method} {u.path}")
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        corpo = json.loads(req.post_data) if req.post_data else None
        try:
            status, dados = self.responder(req.method, u.path, q, corpo)
        except KeyError as e:
            status, dados = 404, {"erro": f"rota {e}"}
        route.fulfill(
            status=status,
            headers={**CORS, "content-type": "application/json"},
            body=json.dumps(dados),
        )

    def filtrar(self, linhas, q, ignorar=()):
        for chave, valor in q.items():
            if chave in ignorar or not valor.startswith("eq."):
                continue
            esperado = unquote(valor[3:])
            linhas = [x for x in linhas if str(x.get(chave)).lower() == esperado.lower()]
        return linhas

    def responder(self, metodo, caminho, q, corpo):  # noqa: C901
        if caminho.endswith("/functions/v1/assistente"):
            return self.funcao(corpo)
        tabela = caminho.rsplit("/", 1)[-1]
        if tabela == "projetos":
            if metodo == "POST":
                pid = self.projeto(**corpo)
                return 201, [self.projetos[pid]]
            alvo = self.filtrar(list(self.projetos.values()), q)
            if metodo == "PATCH":
                for p in alvo:
                    p.update(corpo)
                return 204, None
            if metodo == "DELETE":
                for p in alvo:
                    del self.projetos[p["id"]]
                return 204, None
            return 200, sorted(alvo, key=lambda p: p["updated_at"], reverse=True)
        if tabela == "projeto_secoes":
            if metodo == "POST":
                return 201, [
                    self.salvar_secao(corpo["projeto_id"], corpo["secao_id"], corpo["rascunho"])
                ]
            alvo = self.filtrar(self.secoes, q)
            if metodo == "PATCH":
                for s in alvo:
                    s.update(corpo)
                return 204, None
            return 200, alvo
        if tabela == "mensagens":
            return 200, self.filtrar(self.mensagens, q)
        if tabela == "projeto_secoes_hist":
            return 200, [
                h for h in self.hist if h["projeto_secao_id"] == unquote(q["projeto_secao_id"][3:])
            ]
        if tabela == "projeto_proponente":
            if metodo == "POST":
                self.proponente[corpo["projeto_id"]] = corpo["dados"]
                return 201, None
            pid = unquote(q["projeto_id"][3:])
            return 200, [{"dados": self.proponente[pid]}] if pid in self.proponente else []
        if tabela == "editais":
            return 200, self.filtrar(self.editais, q)
        if tabela == "editais_abertos":
            termo = unquote(q["titulo"][6:]).strip("*").lower()
            return 200, [e for e in self.editais if termo in e["titulo"].lower()]
        raise KeyError(tabela)

    def salvar_secao(self, pid, sid, texto):
        s = next((x for x in self.secoes if x["projeto_id"] == pid and x["secao_id"] == sid), None)
        if not s:
            s = {
                "id": str(uuid.uuid4()),
                "projeto_id": pid,
                "secao_id": sid,
                "rascunho": "",
                "versao": 1,
                "aprovado": False,
                "updated_at": "x",
            }
            self.secoes.append(s)
        if texto != s["rascunho"]:
            if s["rascunho"]:
                self.hist.append(
                    {
                        "projeto_secao_id": s["id"],
                        "rascunho": s["rascunho"],
                        "versao": s["versao"],
                        "created_at": "2026-11-07T13:00:00Z",
                    }
                )
            s["rascunho"], s["versao"], s["aprovado"] = texto, s["versao"] + 1, False
        return s

    def funcao(self, corpo):
        self.chamadas.append(corpo)
        proxima = self.fila.pop(0)
        if isinstance(proxima, tuple):  # (status, corpo de erro)
            return proxima
        pid, sid = corpo["projeto_id"], corpo["secao_id"]
        if corpo.get("mensagem"):
            self.mensagens.append(
                {
                    "projeto_id": pid,
                    "secao_id": sid,
                    "papel": "usuario",
                    "conteudo": {"texto": corpo["mensagem"]},
                    "created_at": "a",
                }
            )
        self.mensagens.append(
            {
                "projeto_id": pid,
                "secao_id": sid,
                "papel": "assistente",
                "conteudo": proxima,
                "created_at": "b",
            }
        )
        if proxima["tipo"] == "rascunho":
            self.salvar_secao(pid, sid, proxima["rascunho"])
        return 200, {
            "limite_caracteres": 1500,
            "uso": {"usados": len(self.chamadas), "limite": 60},
            **proxima,
        }


PERGUNTA = {
    "tipo": "pergunta",
    "perguntas": [
        {
            "id": "tipo_proponente",
            "texto": "Você vai se inscrever como pessoa física, MEI ou coletivo?",
        }
    ],
    "rascunho": "",
    "alertas": [],
    "fatos_novos_ficha": {},
}
RASCUNHO = {
    "tipo": "rascunho",
    "perguntas": [],
    "rascunho": "Resumo da ficha.\n\nO projeto será realizado em [COMPLETAR: cidade] com apoio de [COMPLETAR: parceiros].",
    "alertas": [{"nivel": "aviso", "texto": "Falta informar a cidade."}],
    "fatos_novos_ficha": {},
}


def preparar(navegador, specs, logado=True, largura=1280, altura=900):
    be = Backend(specs)
    pg = nova_pagina(navegador, largura, altura, permissoes=["clipboard-read", "clipboard-write"])
    pg.route(f"{SB}/**", be.tratar)
    pg.route(
        "**/config.js",
        lambda r: r.fulfill(
            content_type="text/javascript",
            body=f'window.EDITAIS_CONFIG = {{ supabaseUrl: "{SB}", supabaseAnonKey: "sb_publishable_teste" }};',
        ),
    )
    pg.route(
        DOCX_CDN, lambda r: r.fulfill(content_type="text/javascript", headers=CORS, body=DOCX_FALSO)
    )
    if logado:
        pg.add_init_script(
            f"localStorage.setItem('editais-culturais:sessao', JSON.stringify({{access_token: '{jwt()}', refresh_token: 'r', expires_at: {int(time.time()) + 3600}}}));"
        )
    return pg, be


def specs_do_site(url):
    import urllib.request

    return json.loads(urllib.request.urlopen(url + "projeto/specs.json").read())


def abrir(pg, url, caminho):
    pg.goto(url + "projeto/" + caminho)
    pg.wait_for_selector("#projeto-raiz h1, #projeto-raiz .msg-form.erro")


def test_sem_login_mostra_login_e_envia_o_link(navegador, url):
    pg, be = preparar(navegador, None, logado=False)
    pedidos = []
    pg.route(
        f"{SB}/auth/v1/otp*",
        lambda r: (
            pedidos.append((r.request.url, json.loads(r.request.post_data))),
            r.fulfill(status=200, headers=CORS, content_type="application/json", body="{}"),
        ),
    )
    abrir(pg, url, "?novo=1&edital=mapas:ce:1")
    assert pg.get_by_role("heading", name="Escreva seu projeto para um edital").is_visible()
    corpo = pg.locator("#projeto-raiz").inner_text()
    assert "Google Gemini" in corpo and "não garante aprovação" in corpo and "sem senha" in corpo
    pg.fill("#email", "ana@exemplo.invalid")
    pg.get_by_role("button", name="Receber link de acesso").click()
    pg.get_by_text("Link enviado.").wait_for()
    assert pedidos[0][1] == {"email": "ana@exemplo.invalid", "create_user": True}
    assert "redirect_to=" in pedidos[0][0] and "novo%3D1" in pedidos[0][0], (
        "volta para o mesmo lugar"
    )
    assert pg.erros == []
    pg.context.close()


def test_link_expirado_mostra_mensagem_amigavel(navegador, url):
    pg, _ = preparar(navegador, None, logado=False)
    pg.goto(url + "projeto/#error=access_denied&error_code=otp_expired&error_description=x")
    pg.wait_for_selector("#projeto-raiz h1")
    assert "expirou" in pg.locator(".msg-form.erro").inner_text()
    assert pg.evaluate("location.hash") == "", "o hash sai da URL"
    pg.context.close()


def test_retorno_do_link_guarda_a_sessao_e_limpa_a_url(navegador, url):
    pg, be = preparar(navegador, None, logado=False)
    be.projeto()
    pg.goto(
        url
        + f"projeto/#access_token={jwt()}&refresh_token=abc&expires_in=3600&token_type=bearer&type=magiclink"
    )
    pg.wait_for_selector("h1:has-text('Meus projetos')")
    assert "access_token" not in pg.url
    assert (
        pg.evaluate("JSON.parse(localStorage.getItem('editais-culturais:sessao')).refresh_token")
        == "abc"
    )
    assert "ana@exemplo.invalid" in pg.locator(".barra-usuario").inner_text()
    pg.context.close()


def test_lista_com_progresso_e_exclusao_em_duas_etapas(navegador, url):
    pg, be = preparar(navegador, None)
    a = be.projeto(
        titulo="Projeto A", edital_titulo="Edital X", edital_url="https://exemplo.invalid/x"
    )
    be.projeto(titulo="Projeto B")
    be.salvar_secao(a, "ficha", "texto")
    be.secoes[0]["aprovado"] = True
    abrir(pg, url, "")
    cartoes = pg.locator(".cartao-projeto")
    assert cartoes.count() == 2
    assert (
        "1 de 6"
        in cartoes.filter(has=pg.get_by_role("heading", name="Projeto A", exact=True)).inner_text()
    )
    assert (
        "Projeto avulso"
        in cartoes.filter(has=pg.get_by_role("heading", name="Projeto B", exact=True)).inner_text()
    )
    botao = cartoes.filter(has=pg.get_by_role("heading", name="Projeto B", exact=True)).get_by_role(
        "button", name="Excluir"
    )
    botao.click()
    assert cartoes.count() == 2, "o primeiro clique só pede confirmação"
    pg.get_by_role("button", name="Confirmar exclusão").click()
    pg.wait_for_function("document.querySelectorAll('.cartao-projeto').length === 1")
    assert len(be.projetos) == 1
    pg.context.close()


def test_novo_projeto_vindo_do_edital_vincula_e_marca_cotas(navegador, url):
    pg, be = preparar(navegador, None)
    abrir(pg, url, "?novo=1&edital=mapas:ce:1")
    assert "Edital Teste de Teatro 01/2026" in pg.locator(".edital-escolhido").inner_text()
    assert pg.input_value("#titulo-projeto").startswith("Projeto para Edital Teste")
    pg.fill("#titulo-projeto", "Oficinas de rua")
    pg.get_by_role("button", name="Criar projeto").click()
    pg.wait_for_url(re.compile(r"\?id="))
    p = next(iter(be.projetos.values()))
    assert (p["edital_id"], p["edital_chave"], p["titulo"]) == (7, "mapas:ce:1", "Oficinas de rua")
    assert p["ficha"] == {"edital_tem_cotas": True, "edital_teto_por_projeto": 80000.0}
    pg.context.close()


def test_novo_projeto_avulso_e_busca_de_edital(navegador, url):
    pg, be = preparar(navegador, None)
    abrir(pg, url, "?novo=1")
    pg.fill("#busca-edital", "teatro")
    pg.get_by_role("button", name="Edital Teste de Teatro 01/2026").click()
    assert "Edital:" in pg.locator(".edital-escolhido").inner_text()
    pg.get_by_role("button", name="Trocar").click()
    pg.fill("#titulo-projeto", "Avulso")
    pg.get_by_role("button", name="Criar projeto").click()
    pg.wait_for_url(re.compile(r"\?id="))
    assert next(iter(be.projetos.values()))["edital_id"] is None
    pg.context.close()


def test_fluxo_completo_conversa_rascunho_aprovacao_e_historico(navegador, url):
    pg, be = preparar(navegador, None)
    pid = be.projeto(
        edital_titulo="Edital X",
        edital_url="https://exemplo.invalid/x",
        ficha={"edital_tem_cotas": True},
    )
    be.fila = [PERGUNTA, RASCUNHO, RASCUNHO]
    abrir(pg, url, f"?id={pid}")
    pg.wait_for_selector(".item-secao")
    # seções: 6 ativas + 6 "em breve" desativadas
    assert pg.locator(".item-secao").count() == 12
    assert pg.locator(".item-secao:disabled").count() == 6
    assert "em breve" in pg.locator(".item-secao").nth(6).inner_text()
    assert pg.locator(".item-secao[aria-current=step]").inner_text().startswith("00")
    assert "não garante aprovação" in pg.locator(".aviso-fixo").first.inner_text()

    # cotas: formulário fora do chat; salva na ficha, nada vai ao assistente
    pg.get_by_label("Pessoa com deficiência").check()
    pg.get_by_role("button", name="Salvar escolha").click()
    pg.get_by_text("Salvo.").wait_for()
    assert be.projetos[pid]["ficha"]["cotas"] == ["pcd"]
    assert be.chamadas == []

    pg.get_by_role("button", name="Começar esta seção").click()
    pg.get_by_text("pessoa física, MEI ou coletivo").wait_for()
    assert be.chamadas[0] == {"projeto_id": pid, "secao_id": "ficha", "acao": "responder"}
    pg.fill("#resposta", "Sou MEI e faço teatro de rua.")
    pg.get_by_role("button", name="Enviar").click()
    pg.wait_for_selector(".painel-rascunho textarea")
    assert be.chamadas[1]["mensagem"] == "Sou MEI e faço teatro de rua."
    assert pg.locator(".msg-usuario").inner_text() == "Sou MEI e faço teatro de rua."
    assert "Atenção:" in pg.locator(".alertas").inner_text()
    assert pg.locator(".completar mark").count() == 2
    assert (
        "2 pontos" in pg.locator(".completar h3").inner_text().lower()
        or "(2)" in pg.locator(".completar h3").inner_text()
    )
    contador = pg.locator("#contador-rascunho").inner_text()
    assert f"{len(RASCUNHO['rascunho'])} / 1500" in contador

    # aprovar com pontos pendentes pede confirmação
    pg.get_by_role("button", name="Aprovar seção").click()
    assert "2 ponto(s)" in pg.locator(".confirma").inner_text()
    pg.get_by_role("button", name="Aprovar mesmo assim").click()
    pg.get_by_role("button", name="Reabrir seção").wait_for()
    assert be.secoes[0]["aprovado"] is True
    assert (
        "1 de 6 aprovadas" in pg.locator(".secoes-menu").inner_text().replace(" ", " ")
        or "1 de 6" in pg.locator(".secoes-menu").inner_text()
    )
    assert "aprovada" in pg.locator(".item-secao").first.inner_text()

    # editar tira a aprovação e cria versão no histórico
    pg.fill("#texto-rascunho", "Texto editado pela pessoa.")
    pg.get_by_role("button", name="Salvar edição").click()
    pg.get_by_role("button", name="Aprovar seção").wait_for()
    assert len(be.hist) == 1 and be.secoes[0]["versao"] == 3
    pg.locator(".historico summary").click()
    pg.get_by_text("Versão 2").first.wait_for()

    # pedir nova versão: instrução vai como mensagem de "redigir"
    pg.get_by_role("button", name="Pedir nova versão").click()
    pg.fill("#instrucao-versao", "mais curto")
    pg.get_by_role("button", name="Gerar nova versão").click()
    for _ in range(50):
        if len(be.chamadas) == 3:
            break
        time.sleep(0.1)
    assert be.chamadas[2] == {
        "projeto_id": pid,
        "secao_id": "ficha",
        "acao": "redigir",
        "mensagem": "mais curto",
    }
    assert pg.erros == []
    pg.context.close()


def test_limite_diario_mostra_mensagem_amigavel_sem_botao_de_repetir(navegador, url):
    pg, be = preparar(navegador, None)
    pid = be.projeto()
    msg = "Você chegou ao limite de 60 interações de hoje com o assistente. Seu trabalho está salvo; volte amanhã para continuar."
    be.fila = [(429, {"erro": "limite_diario", "mensagem": msg})]
    abrir(pg, url, f"?id={pid}&secao=apresentacao")
    pg.get_by_role("button", name="Começar esta seção").click()
    pg.get_by_role("alert").filter(has_text="volte amanhã").wait_for()
    assert pg.get_by_role("button", name="Tentar de novo").count() == 0
    pg.context.close()


def test_erro_da_ia_oferece_tentar_de_novo(navegador, url):
    pg, be = preparar(navegador, None)
    pid = be.projeto()
    be.fila = [
        (
            502,
            {
                "erro": "resposta_invalida",
                "mensagem": "A IA devolveu uma resposta que não consegui entender. Tente de novo.",
            },
        ),
        PERGUNTA,
    ]
    abrir(pg, url, f"?id={pid}&secao=publico")
    pg.get_by_role("button", name="Começar esta seção").click()
    pg.get_by_role("button", name="Tentar de novo").click()
    pg.get_by_text("pessoa física, MEI ou coletivo").wait_for()
    assert len(be.chamadas) == 2
    pg.context.close()


def test_proponente_e_exportacao_docx_e_copiar_por_campo(navegador, url):
    pg, be = preparar(navegador, None)
    pid = be.projeto(
        titulo="Oficinas de rua", edital_titulo="Edital X", ficha={"linguagem": "Teatro"}
    )
    be.salvar_secao(pid, "apresentacao", "Oficinas em Recife. [COMPLETAR: datas]")
    be.secoes[0]["aprovado"] = True
    be.salvar_secao(pid, "justificativa", "Rascunho ainda não aprovado.")
    abrir(pg, url, f"?id={pid}")
    pg.wait_for_selector(".item-secao")

    pg.get_by_role("button", name="Dados do proponente").click()
    assert "nunca são enviados à IA" in pg.locator("dialog").inner_text()
    pg.fill("#prop-nome_completo", "Ana Souza")
    pg.fill("#prop-documento", "123.456.789-09")
    pg.get_by_role("button", name="Salvar dados").click()
    pg.get_by_text("Dados salvos.").wait_for()
    assert be.proponente[pid]["nome_completo"] == "Ana Souza"
    pg.get_by_role("button", name="Fechar").click()

    pg.get_by_role("button", name="Exportar e copiar").click()
    assert "1 seções aprovadas" in pg.locator("dialog").inner_text()
    with pg.expect_download() as dl:
        pg.get_by_role("button", name="Baixar .docx").click()
    assert dl.value.suggested_filename == "projeto-oficinas-de-rua.docx"
    estrutura = pg.evaluate("window.__docx")
    assert "Ana Souza" in estrutura and "Edital X" in estrutura and "Teatro" in estrutura
    assert '"highlight":"yellow"' in estrutura and "[COMPLETAR: datas]" in estrutura
    assert "Rascunho ainda não aprovado" not in estrutura, "só seções aprovadas"
    assert "Apresentação / Resumo" in estrutura and "Justificativa" not in estrutura
    pg.context.close()


def test_copiar_por_campo(navegador, url):
    pg, be = preparar(navegador, None)
    pid = be.projeto()
    be.salvar_secao(pid, "apresentacao", "Texto para colar na plataforma.")
    abrir(pg, url, f"?id={pid}")
    pg.wait_for_selector(".item-secao")
    pg.get_by_role("button", name="Exportar e copiar").click()
    linha = pg.locator(".campo-copia").filter(has_text="Apresentação")
    assert "31 / 1500" in linha.inner_text()
    linha.get_by_role("button", name="Copiar").click()
    pg.get_by_text("Copiado.").wait_for()
    assert pg.evaluate("navigator.clipboard.readText()") == "Texto para colar na plataforma."
    assert (
        pg.locator(".campo-copia")
        .filter(has_text="Ficha")
        .get_by_role("button", name="Copiar")
        .is_disabled()
    )
    pg.context.close()


def test_celular_abas_e_menu_de_secoes(navegador, url):
    pg, be = preparar(navegador, None, largura=390, altura=800)
    pid = be.projeto()
    be.salvar_secao(pid, "ficha", "Rascunho da ficha.")
    abrir(pg, url, f"?id={pid}")
    pg.wait_for_selector(".item-secao", state="attached")
    assert pg.locator(".abas").is_visible()
    assert not pg.locator(".item-secao").first.is_visible(), "menu de seções começa fechado"
    pg.locator(".secoes-menu").click()
    assert pg.locator(".item-secao").first.is_visible()
    pg.locator(".item-secao").nth(1).click()
    assert (
        pg.locator(".painel-chat").is_visible() and not pg.locator(".painel-rascunho").is_visible()
    )
    pg.get_by_role("tab", name="Rascunho").click()
    assert (
        pg.locator(".painel-rascunho").is_visible() and not pg.locator(".painel-chat").is_visible()
    )
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        "sem rolagem horizontal"
    )
    pg.context.close()


def test_desktop_tres_colunas_sem_abas(navegador, url):
    pg, be = preparar(navegador, None)
    pid = be.projeto()
    abrir(pg, url, f"?id={pid}")
    pg.wait_for_selector(".item-secao")
    assert not pg.locator(".abas").is_visible()
    assert pg.locator(".painel-chat").is_visible() and pg.locator(".painel-rascunho").is_visible()
    pg.context.close()


def test_tema_escuro_na_tela_do_projeto(navegador, url):
    pg, be = preparar(navegador, None)
    pid = be.projeto()
    abrir(pg, url, f"?id={pid}")
    pg.wait_for_selector(".item-secao")
    pg.evaluate("document.documentElement.dataset.tema='escuro'")
    pg.wait_for_function("getComputedStyle(document.body).backgroundColor === 'rgb(22, 21, 19)'")
    fundo = pg.evaluate("getComputedStyle(document.body).backgroundColor")
    superficie = pg.evaluate(
        "getComputedStyle(document.querySelector('.painel-chat')).backgroundColor"
    )
    assert fundo == "rgb(22, 21, 19)" and superficie == "rgb(30, 29, 26)"
    pg.context.close()


def test_botao_no_painel_do_edital_leva_ao_assistente(navegador, url, snapshot):
    from e2e_util import abertos

    pg = nova_pagina(navegador)
    edital = abertos(snapshot)[0]
    pg.goto(url + "editais/")
    pg.wait_for_selector("html[data-pronto]")
    pg.locator(f'.card[data-chave="{edital["chave"]}"] h3 button').click()
    link = pg.get_by_role("link", name="Escrever projeto para este edital")
    href = link.get_attribute("href")
    q = parse_qs(urlparse(href).query)
    assert (
        urlparse(href).path.endswith("/projeto/")
        and q["novo"] == ["1"]
        and q["edital"] == [edital["chave"]]
    )
    pg.close()


def test_portal_publico_continua_sem_chamar_o_login(navegador, url):
    pg = nova_pagina(navegador)
    chamadas = []
    pg.on("request", lambda r: chamadas.append(r.url) if "/auth/v1" in r.url else None)
    pg.goto(url + "editais/")
    pg.wait_for_selector("html[data-pronto]")
    assert chamadas == []
    pg.close()


def test_nenhuma_tela_escreve_a_palavra_null(navegador, url):
    pg, be = preparar(navegador, None)
    pid = be.projeto(ficha={})
    for caminho in (
        "",
        "?novo=1",
        "?novo=1&edital=mapas:ce:1",
        f"?id={pid}",
        f"?id={pid}&secao=justificativa",
    ):
        abrir(pg, url, caminho)
        pg.wait_for_timeout(300)
        texto = pg.locator("#projeto-raiz").inner_text()
        assert not re.search(r"\bnull\b|\bundefined\b", texto), f"{caminho!r}: {texto[:200]!r}"
    pg.context.close()


def test_edital_aparece_no_topo_e_nas_regras(navegador, url):
    pg, be = preparar(navegador, None)
    pid = be.projeto(edital_titulo="Edital Teste X", edital_url="https://exemplo.invalid/x")
    abrir(pg, url, f"?id={pid}")
    pg.wait_for_selector(".item-secao")
    topo = pg.locator(".topo-projeto")
    assert "Edital: Edital Teste X" in topo.inner_text()
    assert (
        topo.get_by_role("link", name="Edital Teste X").get_attribute("href")
        == "https://exemplo.invalid/x"
    )
    regras = pg.locator("p.aviso-fixo")
    assert regras.get_by_role("link", name="Edital Teste X").count() == 1
    pg.context.close()
