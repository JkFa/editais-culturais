// Painel lateral com os detalhes do edital. Acessível: role=dialog, aria-modal,
// foco preso, Esc fecha e o foco volta ao card que abriu.
import { TEXTO_SELO_AUTOMATICO, botaoFavorito, badgePrazo, pilulas, raioX, seloAutomatico } from "./cards.js";
import {
  AVISO_LEGAL,
  EXPLICACAO_ELEGIBILIDADE,
  RESPOSTAS_VAZIAS,
  ROTULOS_ELEGIBILIDADE,
  custosCobertosEl,
  ehInternacional,
  elegibilidadeEfetiva,
  linhaEmPortugues,
  orgaoVisivel,
  prazoDaFonte,
  selosInternacionais,
  textoWhatsappInternacional,
  valorOriginalEl,
} from "./internacional.js";
import {
  NAO_INFORMADO,
  el,
  formatarData,
  formatarDiaMes,
  localDoEdital,
  nomeDaFonte,
  textoDaFonte,
  prazoPorExtenso,
  textoWhatsapp,
  urlAssistente,
} from "./formatos.js";

const FOCAVEIS = 'a[href], button:not([disabled]), input, select, textarea, summary, [tabindex]:not([tabindex="-1"])';

const ROTULOS_CAMPO = {
  elegibilidade_br: "Elegibilidade para brasileiros",
  valor_original: "Valor",
  custos_cobertos: "Custos cobertos",
  valor_total: "Valor total",
  teto_por_projeto: "Teto por projeto",
  proponentes: "Quem pode participar",
  marco_legal: "Marco legal",
  uf: "Estado (UF)",
  exige_contrapartida: "Contrapartida",
  inscricao_fim: "Prazo final",
  tipo_apoio: "Tipo de apoio",
  requisitos: "Requisitos",
  municipio: "Município",
  linguagens: "Linguagens",
  resumo_simples: "Resumo simples",
};

const ROTULOS_VIA = {
  regex: "Lido do texto do edital (automático)",
  llm: "Extraído por IA e conferido no texto do edital",
  curadoria: "Conferido por curadoria humana",
  api: "Informado pela plataforma de origem",
};

function secao(titulo, ...conteudo) {
  return el("section", {}, el("h3", {}, titulo), ...conteudo);
}

function textoOuNaoInformado(valor) {
  return valor ? el("p", {}, valor) : el("p", {}, NAO_INFORMADO);
}

function blocoProveniencia(edital) {
  const itens = Object.entries(edital.proveniencia ?? {}).filter(([campo, p]) => p?.trecho && ROTULOS_CAMPO[campo]);
  if (!itens.length) return null;
  return el(
    "details",
    {},
    el("summary", {}, "Como sabemos disso"),
    el(
      "dl",
      {},
      itens.flatMap(([campo, p]) => [
        el("dt", {}, `${ROTULOS_CAMPO[campo]} · ${ROTULOS_VIA[p.via] ?? p.via}`),
        el("dd", {}, `“${p.trecho}”`),
      ]),
    ),
  );
}

const REPO_ISSUES = "https://github.com/JkFa/editais-culturais/issues/new";

/** Link para avisar um erro de dado: abre uma Issue pública já preenchida (sem dados pessoais). */
export function urlReportarErro(edital) {
  const p = new URLSearchParams({
    title: `Erro de dado: ${edital.titulo}`.slice(0, 120),
    body: `Edital: ${edital.titulo}\nPágina oficial: ${edital.url_origem}\n\nO que está errado (prazo, órgão, valor...):\n`,
    labels: "erro-de-dado",
  });
  return `${REPO_ISSUES}?${p.toString()}`;
}

/** Só para editais publicados automaticamente: explica o selo e oferece o aviso de erro. */
function blocoVerificacaoAutomatica(edital) {
  if (edital.verificacao !== "automatica") return null;
  return el(
    "section",
    { class: "verificacao-auto" },
    el("h3", {}, "Verificado automaticamente"),
    el("p", {}, TEXTO_SELO_AUTOMATICO),
    el("p", {}, el("a", { href: urlReportarErro(edital), target: "_blank", rel: "noopener noreferrer" }, "Reportar erro neste edital")),
  );
}

function blocoAlternativas(edital) {
  const alt = edital.fontes_alternativas ?? [];
  if (!alt.length) return null;
  return secao(
    "Também publicado em",
    el(
      "ul",
      { class: "docs" },
      alt.map((a) =>
        el("li", {}, el("a", { href: a.url, target: "_blank", rel: "noopener noreferrer" }, a.via || nomeDaFonte(a.fonte))),
      ),
    ),
  );
}

/** Painel de uma oportunidade internacional: fonte, elegibilidade com aviso e prazo como a fonte informa. */
function conteudoInternacional(edital, { favorito, aoAlternarFavorito, urlPortal, aoFechar, aoCopiar, respostas = RESPOSTAS_VAZIAS }) {
  const efetiva = elegibilidadeEfetiva(edital, respostas);
  const links = [
    el("a", { href: edital.url_origem, target: "_blank", rel: "noopener noreferrer" }, `Chamada original (${edital.via || "fonte"})`),
    edital.url_inscricao && edital.url_inscricao !== edital.url_origem
      ? el("a", { href: edital.url_inscricao, target: "_blank", rel: "noopener noreferrer" }, "Como se candidatar")
      : null,
    edital.url_edital_pdf && edital.url_edital_pdf !== edital.url_origem
      ? el("a", { href: edital.url_edital_pdf, target: "_blank", rel: "noopener noreferrer" }, "Bases em PDF")
      : null,
  ];
  const evidencia = edital.proveniencia?.elegibilidade_br?.trecho;
  return [
    el(
      "div",
      { class: "painel-topo" },
      el(
        "div",
        {},
        el("h2", { id: "painel-titulo", lang: edital.idioma_original ?? undefined }, edital.titulo_original ?? edital.titulo),
        orgaoVisivel(edital) ? el("p", { class: "fonte" }, orgaoVisivel(edital)) : null,
        el("div", { class: "painel-badge" }, badgePrazo(edital)),
      ),
      el(
        "div",
        { class: "painel-acoes" },
        botaoFavorito(edital, favorito, aoAlternarFavorito),
        el("button", { type: "button", class: "btn", id: "painel-fechar", onclick: aoFechar }, "Fechar"),
      ),
    ),
    el(
      "div",
      { class: "painel-corpo" },
      el("p", { class: "linha-pt" }, linhaEmPortugues(edital)),
      secao(
        "Posso me candidatar?",
        selosInternacionais(edital, respostas),
        el("p", {}, EXPLICACAO_ELEGIBILIDADE[efetiva]),
        evidencia ? el("p", { class: "fonte" }, `Trecho da fonte: “${evidencia}”`) : null,
        efetiva === "verificar" ? el("p", { class: "aviso-intl" }, ROTULOS_ELEGIBILIDADE.verificar + ": " + EXPLICACAO_ELEGIBILIDADE.verificar) : null,
      ),
      secao(
        "Prazo",
        el("p", {}, "Termina em ", el("strong", { class: "num" }, prazoDaFonte(edital)), "."),
        el("p", { class: "fonte" }, "Mostramos o prazo como a fonte informa, sem converter para o horário de Brasília."),
      ),
      edital.custos_cobertos?.length ? secao("Custos cobertos (segundo a fonte)", custosCobertosEl(edital)) : null,
      edital.valor_original != null ? secao("Valor", valorOriginalEl(edital)) : null,
      edital.resumo_original ? secao("Trecho da chamada (no idioma original)", el("p", { lang: edital.idioma_original ?? undefined }, edital.resumo_original)) : null,
      edital.resumo_simples && edital.resumo_simples !== edital.resumo_original ? secao("Observação do portal", el("p", {}, edital.resumo_simples)) : null,
      secao("Links", el("div", { class: "links" }, links)),
      el("p", { class: "aviso-intl" }, AVISO_LEGAL),
      blocoAlternativas(edital),
      blocoProveniencia(edital),
      el(
        "p",
        { class: "fonte" },
        `Fonte: ${edital.via || nomeDaFonte(edital.fonte)} · coletado em `,
        edital.coletado_em ? formatarDiaMes(edital.coletado_em) : "data desconhecida",
        edital.verificado_em ? ` · conferido em ${formatarData(`${edital.verificado_em}T12:00:00-03:00`)}` : "",
      ),
    ),
    el(
      "div",
      { class: "painel-rodape" },
      el(
        "button",
        { type: "button", class: "btn btn-primario", id: "copiar-whatsapp", onclick: () => aoCopiar(textoWhatsappInternacional(edital, urlPortal)) },
        "Copiar resumo para WhatsApp",
      ),
    ),
  ];
}

function conteudoDoPainel(edital, opcoes) {
  if (ehInternacional(edital)) return conteudoInternacional(edital, opcoes);
  return conteudoBrasil(edital, opcoes);
}

function conteudoBrasil(edital, { favorito, aoAlternarFavorito, urlPortal, aoFechar, aoCopiar }) {
  const documentos = edital.documentos_exigidos?.length
    ? el("ul", { class: "docs" }, edital.documentos_exigidos.map((d) => el("li", {}, d)))
    : el("p", {}, NAO_INFORMADO);
  const cronograma = edital.cronograma?.length
    ? el(
        "ol",
        { class: "cronograma" },
        edital.cronograma.map((c) => el("li", {}, `${c.etapa}: `, el("span", { class: "num" }, c.data))),
      )
    : null;
  const abertura = edital.inscricao_inicio ? `Inscrições a partir de ${formatarData(edital.inscricao_inicio)}. ` : "";
  const links = [
    el("a", { href: edital.url_origem, target: "_blank", rel: "noopener noreferrer" }, "Página oficial"),
    edital.url_inscricao && edital.url_inscricao !== edital.url_origem
      ? el("a", { href: edital.url_inscricao, target: "_blank", rel: "noopener noreferrer" }, "Fazer inscrição")
      : null,
    edital.url_edital_pdf && edital.url_edital_pdf !== edital.url_origem
      ? el("a", { href: edital.url_edital_pdf, target: "_blank", rel: "noopener noreferrer" }, "Edital em PDF")
      : null,
  ];
  const local = localDoEdital(edital);
  return [
    el(
      "div",
      { class: "painel-topo" },
      el(
        "div",
        {},
        el("h2", { id: "painel-titulo" }, edital.titulo),
        el("p", { class: "fonte" }, edital.orgao_fomento, local ? ` · ${local}` : ""),
        el("div", { class: "painel-badge" }, badgePrazo(edital)),
        seloAutomatico(edital),
      ),
      el(
        "div",
        { class: "painel-acoes" },
        botaoFavorito(edital, favorito, aoAlternarFavorito),
        el("button", { type: "button", class: "btn", id: "painel-fechar", onclick: aoFechar }, "Fechar"),
      ),
    ),
    el(
      "div",
      { class: "painel-corpo" },
      blocoVerificacaoAutomatica(edital),
      secao("Dinheiro", raioX(edital)),
      secao("Quem pode participar", pilulas(edital)),
      secao(
        "Prazo",
        el("p", {}, abertura, "Termina em ", el("strong", { class: "num" }, prazoPorExtenso(edital)), "."),
      ),
      secao("Resumo", textoOuNaoInformado(edital.resumo_original || edital.resumo_simples)),
      edital.pendencias ? secao("Pendência da curadoria", el("p", {}, edital.pendencias)) : null,
      secao("Documentos exigidos", documentos),
      cronograma ? secao("Cronograma", cronograma) : null,
      secao("Links", el("div", { class: "links" }, links)),
      el(
        "p",
        { class: "assistente-chamada" },
        el("a", { class: "btn", href: urlAssistente(edital) }, "Escrever projeto para este edital"),
        el("span", { class: "fonte" }, " Assistente gratuito, com login por e-mail."),
      ),
      blocoAlternativas(edital),
      blocoProveniencia(edital),
      el(
        "p",
        { class: "fonte" },
        `${textoDaFonte(edital)} · coletado em `,
        edital.coletado_em ? formatarDiaMes(edital.coletado_em) : "data desconhecida",
        edital.verificado_em ? ` · verificado em ${formatarData(`${edital.verificado_em}T12:00:00-03:00`)}` : "",
      ),
    ),
    // Fixo na base do painel (e da tela cheia no celular), acima da área segura do iPhone.
    el(
      "div",
      { class: "painel-rodape" },
      el(
        "button",
        {
          type: "button",
          class: "btn btn-primario",
          id: "copiar-whatsapp",
          onclick: () => aoCopiar(textoWhatsapp(edital, urlPortal)),
        },
        "Copiar resumo para WhatsApp",
      ),
    ),
  ];
}

export class Painel {
  constructor(raiz, appRaiz) {
    this.raiz = raiz;
    this.appRaiz = appRaiz;
    this.origem = null;
    this.aberto = false;
    this.aoTeclar = this.aoTeclar.bind(this);
  }

  abrir(edital, opcoes, origem) {
    this.origem = origem ?? this.origem;
    this.chave = edital.chave;
    this.raiz.replaceChildren(
      el("div", { class: "painel-fundo", onclick: opcoes.aoFechar }),
      el(
        "div",
        { class: "painel", role: "dialog", "aria-modal": "true", "aria-labelledby": "painel-titulo", tabindex: "-1" },
        conteudoDoPainel(edital, opcoes),
      ),
    );
    this.dialogo = this.raiz.querySelector(".painel");
    this.aoFecharExterno = opcoes.aoFechar;
    if (!this.aberto) {
      this.aberto = true;
      document.body.classList.add("painel-aberto");
      this.appRaiz.inert = true;
      this.appRaiz.setAttribute("aria-hidden", "true");
      document.addEventListener("keydown", this.aoTeclar);
    }
    this.dialogo.querySelector("#painel-fechar").focus();
    this.dialogo.querySelector(".painel-corpo").scrollTop = 0;
  }

  /** Atualiza só o estado do botão de favorito dentro do painel aberto. */
  marcarFavorito(chave, valor) {
    const botao = this.raiz.querySelector(`[data-favorito="${CSS.escape(chave)}"]`);
    if (botao) botao.setAttribute("aria-pressed", String(valor));
  }

  fechar() {
    if (!this.aberto) return;
    this.aberto = false;
    document.removeEventListener("keydown", this.aoTeclar);
    this.raiz.replaceChildren();
    document.body.classList.remove("painel-aberto");
    this.appRaiz.inert = false;
    this.appRaiz.removeAttribute("aria-hidden");
    // O card pode ter sido recriado (novo filtro, link compartilhado): procura o botão
    // do mesmo edital antes de desistir de devolver o foco.
    const destino = this.origem?.isConnected
      ? this.origem
      : document.querySelector(`.card[data-chave="${CSS.escape(this.chave ?? "")}"] h3 button`);
    destino?.focus();
    this.origem = null;
  }

  aoTeclar(evento) {
    if (evento.key === "Escape") {
      evento.preventDefault();
      this.aoFecharExterno();
      return;
    }
    if (evento.key !== "Tab") return;
    const focaveis = [...this.dialogo.querySelectorAll(FOCAVEIS)].filter((n) => n.offsetParent !== null);
    if (!focaveis.length) return;
    const primeiro = focaveis[0];
    const ultimo = focaveis.at(-1);
    if (evento.shiftKey && (document.activeElement === primeiro || !this.dialogo.contains(document.activeElement))) {
      evento.preventDefault();
      ultimo.focus();
    } else if (!evento.shiftKey && document.activeElement === ultimo) {
      evento.preventDefault();
      primeiro.focus();
    }
  }
}

/** navigator.clipboard com fallback para textarea + execCommand. Devolve true se copiou. */
export async function copiarTexto(texto) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(texto);
      return true;
    }
  } catch {
    // cai no fallback: permissão negada ou contexto não seguro
  }
  const area = el("textarea", { readonly: "", "aria-hidden": "true", style: "position:fixed;top:0;left:-9999px" });
  area.value = texto;
  document.body.append(area);
  area.select();
  let copiou = false;
  try {
    copiou = document.execCommand("copy");
  } catch {
    copiou = false;
  }
  area.remove();
  return copiou;
}
