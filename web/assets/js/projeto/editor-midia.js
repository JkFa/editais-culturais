// Editor do plano de mídia: tabela simples; cada peça paga pode apontar para um item de divulgação do orçamento.
import { el } from "../formatos.js";
import { brl } from "./editor-orcamento.js";
import { barraDeStatus, botao, campo, preencher } from "./editor-base.js";
import { totalDoItem } from "./tabelas.mjs";

export function criarEditorMidia({ c, orcamento }) {
  const raiz = el("div", { class: "editor editor-midia" });
  const corpo = el("div", {});
  const itensDivulgacao = () => (orcamento?.() ?? []).filter((i) => i.etapa === "divulgacao");

  function desenhar() {
    const opcoes = [["", "— sem ligação —"], ...itensDivulgacao().map((i) => [i.id, `${i.item || "item"} (${totalDoItem(i) == null ? "valor a confirmar" : brl(totalDoItem(i))})`])];
    const linhas = c.linhas.map((l) => {
      const ligado = itensDivulgacao().find((i) => i.id === l.orcamento_item_id);
      return el("li", { class: "linha-midia", "data-id": l.id },
        campo(c, l, "peca", { rotulo: "Peça", maxlength: 200, placeholder: "cartaz, vídeo, release…", classe: "col-item" }).no,
        campo(c, l, "canal", { rotulo: "Canal", maxlength: 120, placeholder: "Instagram, rua, imprensa…" }).no,
        campo(c, l, "periodo", { rotulo: "Período", maxlength: 120, placeholder: "antes, durante, depois" }).no,
        campo(c, l, "quantidade", { rotulo: "Quantidade", tipo: "num", classe: "col-qtd" }).no,
        ligado
          ? el("div", { class: "campo-tab col-valor" }, el("span", { class: "rotulo" }, "Custo (do orçamento)"), el("output", { class: "num" }, totalDoItem(ligado) == null ? "a confirmar" : brl(totalDoItem(ligado))))
          : campo(c, l, "custo", { rotulo: "Custo (R$)", tipo: "num", placeholder: "se for paga", classe: "col-valor" }).no,
        campo(c, l, "orcamento_item_id", { rotulo: "Item do orçamento", opcoes, classe: "col-fonte", onInput: () => desenhar() }).no,
        botao("Remover", () => { c.remover(l); desenhar(); }, "btn btn-discreto remover", { "aria-label": `Remover a peça ${l.peca || "sem nome"}` }));
    });
    preencher(corpo,
      c.linhas.length ? el("ul", { class: "linhas" }, linhas) : el("p", { class: "nota" }, "Nenhuma peça ainda."),
      botao("Adicionar peça", () => { const l = c.adicionar({ quantidade: 1 }); desenhar(); raiz.querySelector(`[data-id="${l.id}"] input`)?.focus(); }, "btn"));
  }
  desenhar();
  preencher(raiz, corpo, barraDeStatus(c));
  return { no: raiz, redesenhar: desenhar };
}
