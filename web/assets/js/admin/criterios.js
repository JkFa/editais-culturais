// Revisão dos critérios lidos automaticamente dos editais (só administradores).
// A proteção real é a RLS do banco (tabela `admins`): esta tela nem pede os dados se a pessoa não for
// administradora, mas o que impede qualquer acesso indevido é o servidor.
import { configurado } from "../dados.js";
import { el, formatarDataHora } from "../formatos.js";
import { criarApi } from "../projeto/api.js";
import { dadosDoToken, sessaoAtiva } from "../projeto/auth.js";
import { preencher } from "../projeto/dom.js";
import { ROTULOS_ETAPA } from "../projeto/tabelas.mjs";
import { iniciarTema } from "../tema.js";
import { ETAPAS, SECOES, deFormulario, evidenciasLegiveis, linkDoDocumento, marcaDeRevisao, paraFormulario, patchSemCriterios } from "./criterios-modelo.mjs";

const config = window.EDITAIS_CONFIG ?? {};
const raiz = document.getElementById("admin-raiz");
const q = encodeURIComponent;

function avisar(texto) {
  const alvo = document.getElementById("aviso-live");
  alvo.textContent = texto;
  setTimeout(() => { if (alvo.textContent === texto) alvo.textContent = ""; }, 5000);
}

const mensagem = (titulo, ...corpo) => preencher(raiz, el("h1", {}, titulo), ...corpo);

async function iniciar() {
  iniciarTema();
  if (!configurado(config)) return mensagem("Revisão de critérios", el("p", { class: "msg-form erro" }, "O portal não está configurado com o banco de dados."));
  const sessao = await sessaoAtiva(config).catch(() => null);
  if (!sessao) {
    return mensagem("Revisão de critérios",
      el("p", {}, "Esta área é só para administradores. Entre primeiro pelo assistente (o link chega por e-mail) e volte aqui."),
      el("p", {}, el("a", { class: "btn btn-primario", href: "../../projeto/" }, "Entrar pelo assistente")));
  }
  const api = criarApi(config);
  const eu = dadosDoToken(sessao.access_token);
  let admin = false;
  try {
    admin = (await api.rest("admins?select=user_id&limit=1")).length > 0;
  } catch (e) {
    return mensagem("Revisão de critérios", el("p", { class: "msg-form erro", role: "alert" }, e.message));
  }
  if (!admin) {
    return mensagem("Acesso restrito", el("p", {}, `A conta ${eu.email ?? ""} não é administradora do portal.`), el("p", {}, el("a", { href: "../../projeto/" }, "Voltar ao assistente")));
  }
  const ctx = { api, eu, config };
  const id = new URLSearchParams(location.search).get("edital");
  try {
    await (id ? detalhe(ctx, Number(id)) : lista(ctx));
  } catch (e) {
    preencher(raiz, el("p", { class: "msg-form erro", role: "alert" }, e.message || "Algo deu errado. Recarregue a página."));
  }
}

// ── lista ────────────────────────────────────────────────────────────────────────────────────

const ROTULO_FILA = { pendente: "na fila", processando: "processando", ok: "extraído", erro: "erro", sem_documento: "sem documento" };

async function reprocessar(ctx, editalId) {
  await ctx.api.rest(`extracao_fila?edital_id=eq.${q(editalId)}`, {
    metodo: "PATCH",
    corpo: { status: "pendente", tentativas: 0, ultimo_erro: "reprocessar", atualizado_em: new Date().toISOString() },
    prefer: "return=minimal",
  });
}

async function lista(ctx) {
  const [paraRevisar, problemas] = await Promise.all([
    ctx.api.rest("rpc/admin_criterios_para_revisar", { metodo: "POST", corpo: {} }),
    ctx.api.rest("extracao_fila?status=in.(erro,sem_documento)&select=edital_id,status,ultimo_erro,tentativas,atualizado_em&order=atualizado_em.desc&limit=200"),
  ]);
  const titulos = problemas.length
    ? await ctx.api.rest(`editais?id=in.(${problemas.map((p) => Number(p.edital_id)).join(",")})&select=id,titulo,url_origem`)
    : [];
  const tituloDe = new Map(titulos.map((t) => [t.id, t]));
  let aba = "revisar";

  function desenhar() {
    const itens = aba === "revisar"
      ? paraRevisar.map((c) => el("li", { class: "item-revisao" },
          el("div", {},
            el("strong", {}, c.titulo),
            el("p", { class: "nota" }, `${c.projetos} projeto(s) · extraído em ${c.extraido_em ? formatarDataHora(c.extraido_em) : "—"} · prazo ${c.inscricao_fim ? formatarDataHora(c.inscricao_fim) : "—"}`)),
          el("a", { class: "btn btn-primario", href: `?edital=${c.edital_id}`, "aria-label": `Revisar os critérios de ${c.titulo}` }, "Revisar")))
      : problemas.map((p) => {
          const t = tituloDe.get(p.edital_id);
          const msg = el("span", { class: "msg-form", role: "status" });
          const botao = el("button", { type: "button", class: "btn" }, "Reprocessar");
          botao.addEventListener("click", async () => {
            botao.disabled = true;
            try {
              await reprocessar(ctx, p.edital_id);
              msg.textContent = "Na fila para a próxima extração.";
            } catch (e) {
              msg.className = "msg-form erro";
              msg.textContent = e.message;
              botao.disabled = false;
            }
          });
          return el("li", { class: "item-revisao" },
            el("div", {},
              el("strong", {}, t?.titulo ?? `Edital ${p.edital_id}`),
              el("p", { class: "nota" }, `${ROTULO_FILA[p.status]} · ${p.tentativas} tentativa(s) · ${p.ultimo_erro ?? "sem detalhe"}`),
              t?.url_origem ? el("a", { href: t.url_origem, target: "_blank", rel: "noopener noreferrer" }, "Página do edital") : null),
            el("div", {}, botao, " ", msg));
        });
    preencher(raiz,
      el("h1", {}, "Revisão de critérios dos editais"),
      el("p", { class: "nota" }, "Os critérios abaixo foram lidos do edital por um programa. Eles só deixam de aparecer com o aviso “lidos automaticamente” depois que você os confere. Os editais com mais projetos aparecem primeiro."),
      el("div", { class: "abas", role: "tablist", "aria-label": "Lista" },
        [["revisar", `Para revisar (${paraRevisar.length})`], ["problemas", `Com problema (${problemas.length})`]].map(([k, rot]) =>
          el("button", { type: "button", role: "tab", class: "aba", "aria-selected": String(aba === k), onclick: () => { aba = k; desenhar(); } }, rot))),
      itens.length ? el("ul", { class: "lista-revisao" }, itens) : el("p", { class: "vazio" }, aba === "revisar" ? "Nenhum critério esperando revisão." : "Nenhum edital com problema na extração."));
  }
  desenhar();
}

// ── detalhe ──────────────────────────────────────────────────────────────────────────────────

async function detalhe(ctx, editalId) {
  const [linhas, editais] = await Promise.all([
    ctx.api.rest(`edital_criterios?edital_id=eq.${q(editalId)}&select=*&limit=1`),
    ctx.api.rest(`editais?id=eq.${q(editalId)}&select=id,titulo,url_origem,url_edital_pdf&limit=1`),
  ]);
  const registro = linhas[0];
  const edital = editais[0];
  if (!registro) return mensagem("Critérios não encontrados", el("p", {}, el("a", { href: "./" }, "← Voltar à lista")));
  const f = paraFormulario(registro);
  const evid = new Map(evidenciasLegiveis(registro).map((e) => [e.campo, e]));
  let erros = {};
  const msg = el("p", { class: "msg-form", role: "status" });
  const resumoErros = el("div", { class: "msg-form erro", role: "alert", hidden: true });

  const evidencia = (chave) => {
    const e = evid.get(chave);
    if (!e) return null;
    const link = linkDoDocumento(registro.fonte_url, e.pagina);
    return el("blockquote", { class: "evidencia" }, `“${e.trecho}”`, " ",
      link ? el("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, e.pagina ? `ver no documento (p. ${e.pagina})` : "ver o documento") : null);
  };
  const campo = (rotulo, valor, aoMudar, { chave, ajuda, tipo = "text", rows, opcoes, id } = {}) => {
    const eid = id ?? `f-${Math.random().toString(36).slice(2, 8)}`;
    let entrada;
    if (opcoes) entrada = el("select", { id: eid }, opcoes.map(([v, t]) => el("option", { value: v, selected: v === valor || null }, t)));
    else if (rows) { entrada = el("textarea", { id: eid, rows }); entrada.value = valor; }
    else { entrada = el("input", { id: eid, type: tipo }); entrada.value = valor; }
    entrada.addEventListener(opcoes ? "change" : "input", () => aoMudar(entrada.value));
    const erro = chave && erros[chave];
    if (erro) entrada.setAttribute("aria-invalid", "true");
    return el("div", { class: "campo-tab" }, el("label", { for: eid }, rotulo), entrada,
      ajuda ? el("span", { class: "nota" }, ajuda) : null, erro ? el("span", { class: "erro-campo", role: "alert" }, erro) : null, evidencia(chave ?? ""));
  };

  function desenhar() {
    const doc = registro.fonte_url;
    preencher(raiz,
      el("p", { class: "fonte" }, el("a", { href: "./" }, "← Voltar à lista")),
      el("h1", {}, edital?.titulo ?? `Edital ${editalId}`),
      el("div", { class: "aviso-fixo" },
        el("strong", {}, registro.revisado ? "Critérios conferidos por uma pessoa." : "Critérios lidos automaticamente: confira no documento do edital antes de marcar como revisado."),
        " ", doc ? el("a", { href: doc, target: "_blank", rel: "noopener noreferrer" }, "Abrir o documento original") : "Sem link do documento.",
        registro.modelo_llm ? el("span", { class: "nota" }, ` · lido por ${registro.modelo_llm}`) : null),
      resumoErros,
      el("form", { class: "form-revisao", onsubmit: (ev) => ev.preventDefault() },
        el("fieldset", {}, el("legend", {}, "Valores"),
          el("div", { class: "grade-form" },
            campo("Teto por projeto (R$)", f.teto_orcamento, (v) => (f.teto_orcamento = v), { chave: "teto_orcamento", ajuda: "O máximo que UM projeto pode pedir (não o valor total do edital)." }),
            campo("Teto por proponente (R$)", f.teto_por_proponente, (v) => (f.teto_por_proponente = v), { chave: "teto_por_proponente" }),
            campo("Prazo de execução (meses)", f.prazo_execucao_meses, (v) => (f.prazo_execucao_meses = v), { chave: "prazo_execucao_meses" }),
            campo("O edital exige acessibilidade?", f.exige_acessibilidade, (v) => (f.exige_acessibilidade = v), { chave: "exige_acessibilidade", opcoes: [["", "Não informado"], ["sim", "Sim"], ["nao", "Não"]] })),
          campo("Contrapartida exigida", f.contrapartida_exigida, (v) => (f.contrapartida_exigida = v), { chave: "contrapartida_exigida", rows: 2 })),
        el("fieldset", {}, el("legend", {}, "Percentuais do orçamento por etapa (%)"),
          el("div", { class: "grade-form" }, ETAPAS.flatMap((e) => [
            campo(`${ROTULOS_ETAPA[e]}: máximo (%)`, f.percentuais_max[e], (v) => (f.percentuais_max[e] = v), { chave: `percentuais_max.${e}` }),
            campo(`${ROTULOS_ETAPA[e]}: mínimo (%)`, f.percentuais_min[e], (v) => (f.percentuais_min[e] = v), { chave: `percentuais_min.${e}` }),
          ]))),
        el("fieldset", {}, el("legend", {}, "Rubricas vedadas"),
          campo("Uma por linha", f.rubricas_vedadas, (v) => (f.rubricas_vedadas = v), { rows: 4 }),
          el("div", {}, [...evid.values()].filter((e) => e.campo.startsWith("rubricas_vedadas.")).map((e) => evidencia(e.campo)))),
        el("fieldset", {}, el("legend", {}, "Limites de caracteres por seção"),
          el("div", { class: "grade-form" }, SECOES.map(([id, nome]) => campo(`${nome}`, f.limites_caracteres[id], (v) => (f.limites_caracteres[id] = v), { chave: `limites.${id}` })))),
        el("fieldset", {}, el("legend", {}, "Campos que o edital pede (na ordem)"),
          el("ol", { class: "lista-campos" }, f.campos_exigidos.map((c, i) => el("li", {},
            campo("Nome do campo", c.nome, (v) => (c.nome = v), {}),
            campo("Seção do assistente", c.secao, (v) => (c.secao = v), { opcoes: [["", "— nenhuma —"], ...SECOES] }),
            evidencia(`campos_exigidos.${i}`),
            el("button", { type: "button", class: "btn btn-discreto", onclick: () => { f.campos_exigidos.splice(i, 1); desenhar(); } }, `Remover o campo ${i + 1}`)))),
          el("button", { type: "button", class: "btn", onclick: () => { f.campos_exigidos.push({ nome: "", secao: "" }); desenhar(); } }, "Adicionar campo")),
        el("fieldset", {}, el("legend", {}, "Critérios de avaliação"),
          f.criterios.length ? el("ul", { class: "lista-campos" }, f.criterios.map((c, i) => el("li", {},
            el("div", { class: "grade-form" },
              campo("Nome", c.nome, (v) => (c.nome = v), { chave: `criterios.${i}.nome` }),
              campo("Peso", c.peso, (v) => (c.peso = v), { chave: `criterios.${i}.peso` }),
              campo("Nota máxima", c.nota_max, (v) => (c.nota_max = v), { chave: `criterios.${i}.nota_max` })),
            campo("Descrição", c.descricao, (v) => (c.descricao = v), { rows: 2 }),
            c.trecho_edital ? el("blockquote", { class: "evidencia" }, `“${c.trecho_edital}”`, " ", (() => { const l = linkDoDocumento(registro.fonte_url, c.pagina); return l ? el("a", { href: l, target: "_blank", rel: "noopener noreferrer" }, c.pagina ? `ver no documento (p. ${c.pagina})` : "ver o documento") : null; })()) : null,
            el("button", { type: "button", class: "btn btn-discreto", onclick: () => { f.criterios.splice(i, 1); desenhar(); } }, `Remover o critério ${c.nome || i + 1}`))))
            : el("p", { class: "nota" }, "Nenhum critério lido. Se o documento não os traz (podem estar em anexo separado), deixe vazio.")),
        campo("Observações", f.observacoes, (v) => (f.observacoes = v), { rows: 2 }),
        el("div", { class: "acoes" },
          el("button", { type: "button", class: "btn", onclick: () => salvar(false) }, "Salvar alterações"),
          el("button", { type: "button", class: "btn btn-primario", onclick: () => salvar(true) }, "Marcar como revisado"),
          el("button", { type: "button", class: "btn", onclick: reprocessarAgora }, "Reprocessar"),
          el("button", { type: "button", class: "btn btn-discreto", onclick: semCriterios }, "Sem critérios aproveitáveis"))),
      msg);
  }

  async function gravar(patch, texto) {
    await ctx.api.rest(`edital_criterios?edital_id=eq.${q(editalId)}`, { metodo: "PATCH", corpo: patch, prefer: "return=minimal" });
    Object.assign(registro, patch);
    msg.className = "msg-form";
    msg.textContent = texto;
    avisar(texto);
  }

  async function salvar(marcar) {
    const r = deFormulario(f);
    if (r.erros) {
      erros = r.erros;
      desenhar();
      resumoErros.hidden = false;
      resumoErros.textContent = `Corrija ${Object.keys(r.erros).length} campo(s): ${Object.values(r.erros)[0]}`;
      resumoErros.scrollIntoView?.({ block: "nearest" });
      return;
    }
    erros = {};
    try {
      await gravar({ ...r.patch, ...(marcar ? marcaDeRevisao(ctx.eu.sub) : {}) }, marcar ? "Marcado como revisado." : "Alterações salvas.");
      desenhar();
      avisar(marcar ? "Marcado como revisado." : "Alterações salvas.");
    } catch (e) {
      msg.className = "msg-form erro";
      msg.textContent = e.message;
    }
  }

  async function reprocessarAgora() {
    try {
      await reprocessar(ctx, editalId);
      msg.className = "msg-form";
      msg.textContent = "Na fila: a extração será refeita na próxima execução diária (ou rode o workflow “Sincronizar editais” com “somente_criterios”). Suas edições atuais só são trocadas quando a nova extração chegar.";
    } catch (e) {
      msg.className = "msg-form erro";
      msg.textContent = e.message;
    }
  }

  let confirmando = false;
  async function semCriterios(ev) {
    if (!confirmando) {
      confirmando = true;
      ev.currentTarget.textContent = "Confirmar: apagar o que foi lido";
      setTimeout(() => { confirmando = false; desenhar(); }, 6000);
      return;
    }
    try {
      await gravar({ ...patchSemCriterios(), ...marcaDeRevisao(ctx.eu.sub) }, "Marcado como sem critérios aproveitáveis.");
      Object.assign(f, paraFormulario(registro));
      confirmando = false;
      desenhar();
    } catch (e) {
      msg.className = "msg-form erro";
      msg.textContent = e.message;
    }
  }
  desenhar();
}

iniciar();
