// Editor da equipe: um cartão por pessoa (função, nome ou função, mini currículo, local) e a ligação
// com os itens de cachê do orçamento. Não há campo de CPF, RG ou contato: esses dados ficam fora.
import { el } from "../formatos.js";
import { brl } from "./editor-orcamento.js";
import { barraDeStatus, botao, campo, preencher } from "./editor-base.js";
import { totalDoItem } from "./tabelas.mjs";

export function criarEditorEquipe({ c, orcamento }) {
  const raiz = el("div", { class: "editor editor-equipe" });
  const corpo = el("div", {});

  function ligacoes(l) {
    const itens = orcamento?.() ?? [];
    const marcados = new Set(l.orcamento_item_ids ?? []);
    const soma = itens.filter((i) => marcados.has(i.id)).reduce((s, i) => s + (totalDoItem(i) ?? 0), 0);
    const lista = itens.length
      ? itens.map((i) => {
          const caixa = el("input", { type: "checkbox", checked: marcados.has(i.id) || null });
          caixa.addEventListener("change", () => {
            const atual = new Set(l.orcamento_item_ids ?? []);
            if (caixa.checked) atual.add(i.id);
            else atual.delete(i.id);
            c.alterar(l, "orcamento_item_ids", [...atual]);
            desenhar();
          });
          return el("label", { class: "check" }, caixa, ` ${i.item || "item"} `, el("span", { class: "nota" }, totalDoItem(i) == null ? "(valor a confirmar)" : `(${brl(totalDoItem(i))})`));
        })
      : [el("p", { class: "nota" }, "Cadastre os itens no orçamento para ligá-los a esta pessoa.")];
    return el("details", { class: "ligacoes-equipe", open: marcados.size > 0 || null },
      el("summary", {}, marcados.size ? `Pago no orçamento: ${marcados.size} item(ns), ${brl(soma)}` : "Sem cachê ligado no orçamento"),
      el("div", { class: "grupo-checks" }, lista));
  }

  function desenhar() {
    const cartoes = c.linhas.map((l) =>
      el("li", { class: "cartao-equipe", "data-id": l.id },
        el("div", { class: "grade-equipe" },
          campo(c, l, "funcao", { rotulo: "Função", maxlength: 120, placeholder: "direção, produção, iluminação…" }).no,
          campo(c, l, "nome_artistico_ou_funcao", { rotulo: "Nome artístico (ou repita a função)", maxlength: 160 }).no,
          campo(c, l, "local", { rotulo: "Cidade ou região", maxlength: 120 }).no),
        campo(c, l, "mini_curriculo", { rotulo: "Mini currículo (2 a 3 linhas)", rows: 3, maxlength: 900 }).no,
        ligacoes(l),
        botao("Remover pessoa", () => { c.remover(l); desenhar(); }, "btn btn-discreto remover", { "aria-label": `Remover ${l.nome_artistico_ou_funcao || l.funcao || "pessoa"} da equipe` })));
    preencher(corpo,
      el("p", { class: "nota" }, "Não escreva CPF, RG, endereço, e-mail nem telefone aqui: esses dados ficam só no formulário do proponente."),
      c.linhas.length ? el("ul", { class: "cartoes" }, cartoes) : el("p", { class: "nota" }, "Ninguém na equipe ainda."),
      botao("Adicionar pessoa", () => { const l = c.adicionar({ orcamento_item_ids: [] }); desenhar(); raiz.querySelector(`[data-id="${l.id}"] input`)?.focus(); }, "btn"));
  }
  desenhar();
  preencher(raiz, corpo, barraDeStatus(c));
  return { no: raiz, redesenhar: desenhar };
}
