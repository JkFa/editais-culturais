// Tela do projeto: seções à esquerda, chat no meio, rascunho à direita (abas no celular).
import { formatarDataHora, el } from "../formatos.js";
import { ErroApi } from "./api.js";
import { preencher } from "./dom.js";
import { abrirExportacao } from "./exportar.js";
import { abrirProponente } from "./proponente.js";
import { perguntasAplicaveis, secoesDoFluxo } from "./secoes.mjs";
import { AVISO_IA, AVISO_PRIVACIDADE, COTAS, ROTULO_STATUS } from "./textos.js";

const REGEX_COMPLETAR = /\[COMPLETAR:[^\]]*\]/g;
export const pontosACompletar = (texto) => [...texto.matchAll(REGEX_COMPLETAR)].map((m) => ({ texto: m[0], inicio: m.index, fim: m.index + m[0].length }));

export async function telaProjeto(ctx, id, secaoPedida) {
  const { api, specs, raiz } = ctx;
  const projeto = await api.carregarProjeto(id);
  if (!projeto) throw new ErroApi(404, "Projeto não encontrado. Ele pode ter sido excluído.");
  const [linhas, comConversa] = await Promise.all([api.secoesDoProjeto(id), api.secoesComConversa(id)]);
  const secoes = secoesDoFluxo(specs.secoes, projeto.fluxo);
  const estado = {
    projeto,
    linhas: new Map(linhas.map((l) => [l.secao_id, l])),
    comConversa,
    atual: null,
    mensagens: [],
    carregando: false,
    erro: null,
    alertas: [],
    limite: null,
    uso: null,
    painel: "chat",
    sujo: false,
  };
  const specDe = (sid) => secoes.find((s) => s.id === sid);
  const linhaDe = (sid) => estado.linhas.get(sid) ?? null;
  const ativas = secoes.filter((s) => s.ativo !== false);

  function statusDe(s) {
    if (s.ativo === false) return "em_breve";
    const l = linhaDe(s.id);
    if (l?.aprovado) return "aprovada";
    if (l?.rascunho) return "rascunho";
    return estado.comConversa.has(s.id) ? "andamento" : "nao_iniciada";
  }
  const aprovadas = () => ativas.filter((s) => linhaDe(s.id)?.aprovado).length;

  // ───────────────────────── esqueleto
  const barraSecoes = el("nav", { class: "secoes", "aria-label": "Seções do projeto" });
  const painelChat = el("section", { class: "painel-chat", "aria-labelledby": "titulo-chat" });
  const painelRascunho = el("section", { class: "painel-rascunho", "aria-labelledby": "titulo-rascunho" });
  const abas = el("div", { class: "abas", role: "tablist", "aria-label": "Painel" });
  const grade = el("div", { class: "projeto-grade", "data-painel": "chat" }, barraSecoes, painelChat, painelRascunho);

  function mudarPainel(qual) {
    estado.painel = qual;
    grade.dataset.painel = qual;
    renderAbas();
  }
  function renderAbas() {
    preencher(abas, 
      ...[["chat", "Conversa"], ["rascunho", "Rascunho"]].map(([k, rot]) =>
        el("button", { type: "button", role: "tab", class: "aba", "aria-selected": String(estado.painel === k), onclick: () => mudarPainel(k) }, rot)),
    );
  }

  // ───────────────────────── barra lateral
  let menuAberto = false;
  function renderSecoes() {
    const lista = el("ol", { class: "lista-secoes" },
      secoes.map((s) => {
        const st = statusDe(s);
        const desativada = st === "em_breve";
        return el("li", {},
          el("button", {
            type: "button",
            class: `item-secao st-${st}`,
            disabled: desativada,
            "aria-current": estado.atual === s.id ? "step" : null,
            onclick: () => abrirSecao(s.id),
          }, el("span", { class: "num" }, String(s.ordem).padStart(2, "0")), el("span", { class: "nome" }, s.titulo), el("span", { class: "status" }, ROTULO_STATUS[st])));
      }));
    preencher(barraSecoes, 
      el("button", { type: "button", class: "btn secoes-menu", "aria-expanded": String(menuAberto), "aria-controls": "lista-secoes", onclick: () => { menuAberto = !menuAberto; renderSecoes(); } },
        `Seções · ${aprovadas()} de ${ativas.length} aprovadas`),
      el("div", { id: "lista-secoes", class: "secoes-corpo", hidden: false, "data-aberto": String(menuAberto) }, lista),
    );
  }

  // ───────────────────────── chat
  function mensagemNaTela(m) {
    if (m.papel === "usuario") return el("div", { class: "msg msg-usuario" }, el("p", {}, m.conteudo?.texto ?? ""));
    const c = m.conteudo ?? {};
    const corpo = c.tipo === "rascunho"
      ? [el("p", {}, "Preparei o rascunho desta seção. Revise e edite no painel ao lado. Se algo estiver errado, responda aqui ou peça uma nova versão.")]
      : (c.perguntas ?? []).map((p) => el("p", {}, p.texto));
    const notas = (c.alertas ?? []).filter((a) => c.tipo !== "rascunho").map((a) => el("p", { class: `nota-msg ${a.nivel}` }, a.texto));
    return el("div", { class: "msg msg-assistente" }, el("span", { class: "sr-only" }, "Assistente: "), corpo, notas);
  }

  function formularioCotas() {
    const f = estado.projeto.ficha ?? {};
    if (estado.atual !== "ficha" || !f.edital_tem_cotas) return null;
    const marcadas = new Set(f.cotas ?? []);
    const caixas = COTAS.map(([k, rot]) => el("label", { class: "check" }, el("input", { type: "checkbox", value: k, checked: marcadas.has(k) || null }), rot));
    const usar = el("input", { type: "checkbox", checked: f.cotas_usar_no_texto === true || null });
    const msg = el("span", { class: "msg-form", role: "status" });
    return el("form", {
      class: "form-cotas",
      onsubmit: async (ev) => {
        ev.preventDefault();
        const cotas = caixas.map((c) => c.querySelector("input")).filter((i) => i.checked).map((i) => i.value);
        try {
          const ficha = { ...estado.projeto.ficha, cotas, cotas_usar_no_texto: usar.checked };
          await api.atualizarProjeto(estado.projeto.id, { ficha });
          estado.projeto.ficha = ficha;
          msg.textContent = "Salvo.";
        } catch (e) {
          msg.textContent = e.message;
        }
      },
    },
      el("fieldset", {},
        el("legend", {}, "O edital tem cotas ou pontuação extra. Você quer concorrer em alguma? (opcional)"),
        el("p", { class: "nota" }, "Esta resposta fica só na sua ficha: não é enviada à IA nem usada nos textos, a menos que você marque a opção abaixo."),
        el("div", { class: "grupo-checks" }, caixas),
        el("label", { class: "check" }, usar, "Posso citar isto nos textos do projeto"),
        el("button", { type: "submit", class: "btn" }, "Salvar escolha"), " ", msg));
  }

  function renderChat() {
    const s = specDe(estado.atual);
    if (!s) {
      preencher(painelChat, el("h2", { id: "titulo-chat" }, "Conversa"), el("p", { class: "vazio" }, "Escolha uma seção ao lado para começar."));
      return;
    }
    const log = el("div", { class: "chat-log", role: "log", "aria-live": "polite", "aria-label": `Conversa da seção ${s.titulo}` }, estado.mensagens.map(mensagemNaTela));
    if (estado.carregando) log.append(el("p", { class: "digitando", role: "status" }, "O assistente está escrevendo…"));

    const campo = el("textarea", { id: "resposta", rows: 3, maxlength: 4000, "aria-describedby": "dica-resposta", disabled: estado.carregando || null });
    const iniciada = estado.mensagens.length > 0;
    const mandar = (acao) => {
      const texto = campo.value.trim();
      if (acao === "responder" && iniciada && !texto) {
        campo.focus();
        return;
      }
      campo.value = "";
      enviar(acao, texto || undefined);
    };
    campo.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault();
        mandar("responder");
      }
    });

    const entrada = iniciada
      ? el("div", { class: "chat-entrada" },
          el("label", { for: "resposta" }, "Sua resposta"),
          campo,
          el("p", { id: "dica-resposta", class: "nota" }, "Ctrl+Enter envia. Não escreva CPF, endereço nem telefone aqui."),
          el("div", { class: "acoes" },
            el("button", { type: "button", class: "btn btn-primario", disabled: estado.carregando || null, onclick: () => mandar("responder") }, "Enviar"),
            el("button", { type: "button", class: "btn", disabled: estado.carregando || null, onclick: () => mandar("redigir") }, "Redigir agora com o que tenho")))
      : el("div", { class: "chat-entrada" },
          el("p", {}, `Vou fazer algumas perguntas sobre "${s.titulo}" e, no fim, escrever um rascunho só com o que você contar.`),
          el("button", { type: "button", class: "btn btn-primario", disabled: estado.carregando || null, onclick: () => enviar("responder") }, "Começar esta seção"));

    const erro = estado.erro
      ? el("div", { class: "msg-form erro", role: "alert" }, estado.erro.mensagem, estado.erro.repetir ? [" ", el("button", { type: "button", class: "btn", onclick: estado.erro.repetir }, "Tentar de novo")] : null)
      : null;
    preencher(painelChat, 
      el("h2", { id: "titulo-chat" }, s.titulo),
      formularioCotas(),
      log,
      erro,
      entrada,
      estado.uso ? el("p", { class: "nota" }, `Interações de hoje: ${estado.uso.usados} de ${estado.uso.limite}.`) : null,
    );
    log.scrollTop = log.scrollHeight;
  }

  async function atualizarDoServidor() {
    const [linhas, p] = await Promise.all([api.secoesDoProjeto(estado.projeto.id), api.carregarProjeto(estado.projeto.id)]);
    estado.linhas = new Map(linhas.map((l) => [l.secao_id, l]));
    if (p) estado.projeto = p;
  }

  async function enviar(acao, mensagem) {
    if (estado.carregando) return;
    const sid = estado.atual;
    if (estado.sujo && acao !== "responder") await salvar(true);
    const otimista = mensagem ? { papel: "usuario", conteudo: { texto: mensagem } } : null;
    if (otimista) estado.mensagens.push(otimista);
    estado.carregando = true;
    estado.erro = null;
    renderChat();
    try {
      const r = await api.assistente({ projeto_id: estado.projeto.id, secao_id: sid, acao, ...(mensagem ? { mensagem } : {}) });
      if (estado.atual !== sid) return;
      estado.mensagens.push({ papel: "assistente", conteudo: r });
      estado.limite = r.limite_caracteres;
      estado.uso = r.uso;
      estado.alertas = r.alertas ?? [];
      estado.comConversa.add(sid);
      await atualizarDoServidor();
      if (r.tipo === "rascunho") {
        ctx.avisar("Rascunho pronto.");
        mudarPainel("rascunho");
      }
    } catch (e) {
      if (otimista) estado.mensagens.splice(estado.mensagens.indexOf(otimista), 1);
      estado.erro = { mensagem: e.message, repetir: e.status === 429 ? null : () => enviar(acao, mensagem) };
    } finally {
      estado.carregando = false;
      if (estado.atual === sid) {
        renderChat();
        renderRascunho();
        renderSecoes();
      }
    }
  }

  // ───────────────────────── rascunho
  let areaTexto = null;
  const limiteDe = (s) => estado.limite ?? s.limite_caracteres_padrao;

  function alertasDaUltimaResposta() {
    const ultima = [...estado.mensagens].reverse().find((m) => m.papel === "assistente" && m.conteudo?.tipo === "rascunho");
    return ultima?.conteudo?.alertas ?? [];
  }

  async function salvar(silencioso = false) {
    const s = specDe(estado.atual);
    if (!s || !areaTexto) return;
    try {
      const antes = Boolean(linhaDe(s.id)?.aprovado);
      const linha = await api.salvarRascunho(estado.projeto.id, s.id, areaTexto.value);
      estado.linhas.set(s.id, { ...(linhaDe(s.id) ?? {}), ...linha, secao_id: s.id, rascunho: areaTexto.value });
      estado.sujo = false;
      if (!silencioso) ctx.avisar("Rascunho salvo.");
      renderSecoes();
      // Editar tira a aprovação no servidor: o painel precisa mostrar "Aprovar seção" de novo.
      if (antes !== Boolean(linhaDe(s.id)?.aprovado) || !silencioso) renderRascunho();
    } catch (e) {
      estado.erro = { mensagem: e.message };
      renderChat();
    }
  }

  function renderRascunho() {
    const s = specDe(estado.atual);
    if (!s) {
      areaTexto = null;
      preencher(painelRascunho, el("h2", { id: "titulo-rascunho" }, "Rascunho"), el("p", { class: "vazio" }, "O rascunho da seção aparece aqui."));
      return;
    }
    const linha = linhaDe(s.id);
    const limite = limiteDe(s);
    const texto = linha?.rascunho ?? "";
    if (!texto && !estado.sujo) {
      areaTexto = null;
      preencher(painelRascunho, 
        el("h2", { id: "titulo-rascunho" }, "Rascunho"),
        el("p", { class: "vazio" }, "O rascunho aparece aqui quando você terminar as perguntas, ou quando clicar em \"Redigir agora com o que tenho\"."),
      );
      return;
    }
    areaTexto = el("textarea", { id: "texto-rascunho", rows: 14, "aria-describedby": "contador-rascunho" });
    areaTexto.value = texto;
    const contador = el("p", { id: "contador-rascunho", class: "contador" });
    const listaCompletar = el("div", { class: "completar" });
    const salvarBtn = el("button", { type: "button", class: "btn", disabled: true, onclick: () => salvar() }, "Salvar edição");

    function atualizar() {
      const n = areaTexto.value.length;
      preencher(contador, el("span", { class: n > limite ? "num excedeu" : "num" }, `${n} / ${limite}`), " caracteres", n > limite ? el("strong", { class: "excedeu" }, ` · corte ${n - limite}`) : null);
      const pontos = pontosACompletar(areaTexto.value);
      preencher(listaCompletar, 
        ...(pontos.length
          ? [el("h3", {}, `Pontos para completar (${pontos.length})`),
              el("ul", {}, pontos.map((p) => el("li", {}, el("mark", {}, p.texto), " ",
                el("button", { type: "button", class: "btn btn-discreto", onclick: () => { areaTexto.focus(); areaTexto.setSelectionRange(p.inicio, p.fim); } }, "Ir para o ponto"))))]
          : []),
      );
      salvarBtn.disabled = !estado.sujo;
    }
    areaTexto.addEventListener("input", () => { estado.sujo = true; atualizar(); });
    areaTexto.addEventListener("blur", () => { if (estado.sujo) salvar(true); });

    const alertas = (estado.alertas.length ? estado.alertas : alertasDaUltimaResposta());
    const aprovado = Boolean(linha?.aprovado);
    const confirmacao = el("div", { class: "confirma", hidden: true, role: "alert" });
    const nova = el("div", { class: "nova-versao", hidden: true });
    const instr = el("input", { id: "instrucao-versao", type: "text", maxlength: 300, placeholder: "Ex.: deixe mais curto, destaque o território" });
    nova.append(el("label", { for: "instrucao-versao" }, "O que você quer mudar? (opcional)"), instr,
      el("button", { type: "button", class: "btn btn-primario", onclick: () => { nova.hidden = true; mudarPainel("chat"); enviar("redigir", instr.value.trim() || undefined); } }, "Gerar nova versão"));

    async function aprovar(ignorarPontos) {
      if (estado.sujo) await salvar(true);
      const pontos = pontosACompletar(areaTexto.value);
      if (pontos.length && !ignorarPontos) {
        confirmacao.hidden = false;
        preencher(confirmacao, `Ainda há ${pontos.length} ponto(s) com [COMPLETAR]. `, el("button", { type: "button", class: "btn", onclick: () => aprovar(true) }, "Aprovar mesmo assim"));
        return;
      }
      try {
        await api.definirAprovacao(estado.projeto.id, s.id, true);
        estado.linhas.set(s.id, { ...linhaDe(s.id), aprovado: true });
        ctx.avisar("Seção aprovada.");
        renderRascunho();
        renderSecoes();
      } catch (e) {
        estado.erro = { mensagem: e.message };
        renderChat();
      }
    }

    const historico = el("details", { class: "historico" }, el("summary", {}, "Versões anteriores"));
    let carregouHist = false;
    historico.addEventListener("toggle", async () => {
      if (!historico.open || carregouHist || !linha?.id) return;
      carregouHist = true;
      try {
        const versoes = await api.historico(linha.id);
        historico.append(versoes.length
          ? el("ul", {}, versoes.map((v) => el("li", {}, el("details", {}, el("summary", {}, `Versão ${v.versao} · ${formatarDataHora(v.created_at)}`), el("pre", { class: "versao" }, v.rascunho)))))
          : el("p", { class: "nota" }, "Ainda não há versões anteriores."));
      } catch (e) {
        historico.append(el("p", { class: "msg-form erro" }, e.message));
      }
    });

    preencher(painelRascunho, 
      el("h2", { id: "titulo-rascunho" }, "Rascunho", aprovado ? el("span", { class: "selo" }, "aprovada") : null),
      alertas.length ? el("ul", { class: "alertas" }, alertas.map((a) => el("li", { class: a.nivel }, el("strong", {}, a.nivel === "aviso" ? "Atenção: " : "Dica: "), a.texto))) : null,
      el("label", { for: "texto-rascunho", class: "sr-only" }, `Texto da seção ${s.titulo}`),
      areaTexto,
      contador,
      listaCompletar,
      aprovado ? el("p", { class: "nota" }, "Editar o texto tira a aprovação desta seção.") : null,
      el("div", { class: "acoes" },
        salvarBtn,
        aprovado
          ? el("button", { type: "button", class: "btn", onclick: () => { mudarPainel("chat"); enviar("reabrir"); } }, "Reabrir seção")
          : el("button", { type: "button", class: "btn btn-primario", onclick: () => aprovar(false) }, "Aprovar seção"),
        el("button", { type: "button", class: "btn", onclick: () => { nova.hidden = !nova.hidden; if (!nova.hidden) instr.focus(); } }, "Pedir nova versão"),
        el("button", { type: "button", class: "btn btn-discreto voltar-chat", onclick: () => mudarPainel("chat") }, "Voltar ao chat")),
      confirmacao,
      nova,
      historico,
    );
    atualizar();
  }

  // ───────────────────────── navegação entre seções
  async function abrirSecao(sid) {
    if (estado.sujo) await salvar(true);
    estado.atual = sid;
    estado.erro = null;
    estado.alertas = [];
    estado.mensagens = [];
    estado.limite = null;
    estado.sujo = false;
    menuAberto = false;
    history.replaceState(null, "", `?id=${encodeURIComponent(estado.projeto.id)}&secao=${encodeURIComponent(sid)}`);
    renderSecoes();
    renderChat();
    renderRascunho();
    try {
      estado.mensagens = await api.mensagens(estado.projeto.id, sid);
    } catch (e) {
      estado.erro = { mensagem: e.message };
    }
    if (estado.atual !== sid) return;
    renderChat();
    renderRascunho();
  }

  // ───────────────────────── cabeçalho
  const editalLink = projeto.edital_url
    ? el("a", { href: projeto.edital_url, target: "_blank", rel: "noopener noreferrer" }, projeto.edital_titulo ?? "edital oficial")
    : null;
  preencher(raiz, 
    ctx.barra,
    el("div", { class: "topo-projeto" },
      el("div", {},
        el("p", { class: "fonte" }, el("a", { href: "./" }, "← Meus projetos")),
        el("h1", {}, projeto.titulo),
        el("p", { class: "fonte" }, projeto.edital_titulo ? ["Edital: ", editalLink ?? projeto.edital_titulo] : "Projeto avulso (sem edital)")),
      el("div", { class: "acoes" },
        el("button", { type: "button", class: "btn", onclick: () => abrirProponente(ctx, estado.projeto) }, "Dados do proponente"),
        el("button", { type: "button", class: "btn", onclick: () => abrirExportacao(ctx, { projeto: estado.projeto, secoes: ativas, linhaDe }) }, "Exportar e copiar"))),
    el("p", { class: "aviso-fixo" }, AVISO_IA, " ", editalLink ? ["Regras: ", editalLink, "."] : null),
    el("details", { class: "aviso-fixo" }, el("summary", {}, "Privacidade: o que vai para a IA"), el("p", {}, AVISO_PRIVACIDADE)),
    abas,
    grade,
  );
  renderAbas();
  renderSecoes();
  renderChat();
  renderRascunho();
  const inicial = secaoPedida && specDe(secaoPedida)?.ativo !== false ? secaoPedida : (ativas.find((s) => statusDe(s) !== "aprovada") ?? ativas[0])?.id;
  if (inicial) await abrirSecao(inicial);
}
