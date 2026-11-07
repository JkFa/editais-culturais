// Card de edital (leitura em 5 segundos) e blocos compartilhados com o painel.
import {
  NAO_INFORMADO,
  el,
  formatarMoeda,
  listaDeProponentes,
  localDoEdital,
} from "./formatos.js";
import { estadoDoPrazo } from "./prazos.js";

const ICONE_FAVORITO =
  '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4.5L5 21V4a1 1 0 0 1 1-1z"/></svg>';

export function badgePrazo(edital, agora = new Date()) {
  const { tipo, texto } = estadoDoPrazo(edital, agora);
  const badge = el("span", { class: `badge-prazo ${tipo}` }, texto);
  badge.dataset.fim = edital.inscricao_fim;
  badge.dataset.horario = edital.fim_tem_horario ? "1" : "0";
  return badge;
}

/** Recalcula o texto de todos os badges (chamado a cada minuto). */
export function atualizarBadges(raiz, agora = new Date()) {
  for (const badge of raiz.querySelectorAll(".badge-prazo[data-fim]")) {
    const { tipo, texto } = estadoDoPrazo(
      { inscricao_fim: badge.dataset.fim, fim_tem_horario: badge.dataset.horario === "1" },
      agora,
    );
    badge.className = `badge-prazo ${tipo}`;
    if (badge.textContent !== texto) badge.textContent = texto;
  }
}

/** Raio-X financeiro. Valor nulo nunca vira zero: mostra "não informado". */
export function raioX(edital) {
  if (edital.tipo_apoio === "cessao_de_espaco") {
    return el("p", { class: "pilula marco" }, "Cessão de espaço (sem repasse)");
  }
  const linha = (rotulo, valor) =>
    el(
      "div",
      {},
      el("dt", {}, rotulo),
      valor == null
        ? el("dd", { class: "nulo" }, NAO_INFORMADO)
        : el("dd", { class: "num" }, formatarMoeda(valor)),
    );
  if (edital.valor_total == null && edital.teto_por_projeto == null) {
    return el("dl", { class: "raio-x" }, linha("Valor", null));
  }
  return el(
    "dl",
    { class: "raio-x" },
    linha("Total", edital.valor_total),
    linha("Teto por projeto", edital.teto_por_projeto),
  );
}

export function pilulas(edital) {
  const itens = [];
  const quem = listaDeProponentes(edital);
  if (quem.length) for (const q of quem) itens.push(el("li", { class: "pilula" }, q));
  else itens.push(el("li", { class: "pilula aviso" }, "Elegibilidade não informada"));
  if (edital.marco_legal) itens.push(el("li", { class: "pilula marco" }, edital.marco_legal));
  return el("ul", { class: "pilulas", "aria-label": "Elegibilidade e marco legal" }, itens);
}

export function botaoFavorito(edital, favorito, aoAlternar) {
  const botao = el("button", {
    type: "button",
    class: "btn-fav",
    "aria-pressed": String(favorito),
    "aria-label": `Favoritar: ${edital.titulo}`,
    "data-favorito": edital.chave,
    onclick: (evento) => {
      evento.stopPropagation();
      aoAlternar(edital);
    },
  });
  botao.insertAdjacentHTML("beforeend", ICONE_FAVORITO);
  return botao;
}

export function criarCard(edital, { favorito, aoAbrir, aoAlternarFavorito, agora = new Date() }) {
  const titulo = el(
    "button",
    {
      type: "button",
      "aria-haspopup": "dialog",
      onclick: (e) => aoAbrir(edital, e.currentTarget),
    },
    edital.titulo,
  );
  const local = localDoEdital(edital);
  return el(
    "li",
    {},
    el(
      "article",
      { class: "card", dataset: { chave: edital.chave } },
      el("h3", {}, titulo),
      el("p", { class: "orgao" }, edital.orgao_fomento, local ? ` · ${local}` : ""),
      badgePrazo(edital, agora),
      raioX(edital),
      pilulas(edital),
      edital.resumo_simples ? el("p", { class: "resumo" }, edital.resumo_simples) : null,
      botaoFavorito(edital, favorito, aoAlternarFavorito),
    ),
  );
}

/** Favorito cujo edital saiu da base: mostra o que foi salvo, sem inventar nada. */
export function criarCardRemovido(salvo, { aoRemover }) {
  const link = salvo.url_origem
    ? el("a", { href: salvo.url_origem, target: "_blank", rel: "noopener noreferrer" }, "Ver página oficial")
    : null;
  return el(
    "li",
    {},
    el(
      "article",
      { class: "card card-removido", dataset: { chave: salvo.chave } },
      el("h3", {}, salvo.titulo),
      el("p", { class: "orgao" }, salvo.orgao_fomento),
      el("span", { class: "badge-prazo encerrado" }, "Encerrado ou removido"),
      link,
      el(
        "button",
        { type: "button", class: "btn btn-discreto", onclick: () => aoRemover(salvo.chave) },
        "Remover dos favoritos",
      ),
    ),
  );
}
