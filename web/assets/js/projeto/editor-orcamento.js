// Editor do orçamento: tabela agrupada por etapa, subtotal por etapa, total geral, barra contra o teto
// do edital e % por etapa contra `percentuais_max`. Erros aparecem na própria linha. O servidor
// recalcula tudo de novo antes de gravar.
import { el } from "../formatos.js";
import { barraDeStatus, botao, campo, preencher } from "./editor-base.js";
import { ETAPAS, ROTULOS_ETAPA, resumoOrcamento, totalDoItem, valorAConfirmar } from "./tabelas.mjs";

export const brl = (n) => `R$ ${Number(n ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (x) => `${(x * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;

export function criarEditorOrcamento({ c, criterios, ficha, etapaEmFoco = null }) {
  const raiz = el("div", { class: "editor editor-orcamento" });
  const resumoEl = el("div", { class: "resumo-orcamento", "aria-live": "polite" });
  const corpo = el("div", { class: "etapas" });
  const teto = () => Number(criterios?.teto_orcamento) || Number(ficha?.edital_teto_por_projeto) || 0;
  const maximos = () => criterios?.percentuais_max ?? {};
  const minimos = () => criterios?.percentuais_min ?? {};
  const atualizadores = [];

  function desenharResumo() {
    const r = resumoOrcamento(c.linhas);
    const t = teto();
    const acima = t > 0 && r.total > t;
    const barra = t > 0
      ? el("div", { class: "barra-teto" },
          el("div", { class: `barra-teto-meter${acima ? " acima" : ""}`, role: "meter", "aria-label": "Total do orçamento em relação ao teto do edital", "aria-valuemin": 0, "aria-valuemax": t, "aria-valuenow": Math.min(r.total, t * 1.5) },
            el("span", { style: `width:${Math.min(100, (r.total / t) * 100)}%` })),
          el("p", { class: acima ? "erro-campo" : "nota" }, acima ? `Passa do teto em ${brl(r.total - t)} (teto ${brl(t)}).` : `${pct(r.total / t)} do teto de ${brl(t)}.`))
      : el("p", { class: "nota" }, "O edital não informa teto de orçamento (ou ainda não foi lido).");
    const porEtapa = ETAPAS.filter((e) => r.porEtapa[e].itens || maximos()[e] != null || minimos()[e] != null).map((e) => {
      const b = r.porEtapa[e];
      const max = maximos()[e];
      const min = minimos()[e];
      const fora = (max != null && b.percentual > max + 1e-9) || (min != null && r.total > 0 && b.percentual < min - 1e-9);
      return el("li", { class: fora ? "fora-do-limite" : "" },
        el("span", {}, ROTULOS_ETAPA[e]), " ", el("span", { class: "num" }, `${pct(b.percentual)}`),
        max != null ? el("span", { class: "nota" }, ` (máx. ${pct(Number(max))})`) : null,
        min != null ? el("span", { class: "nota" }, ` (mín. ${pct(Number(min))})`) : null,
        fora ? el("strong", {}, " ← fora do limite do edital") : null);
    });
    preencher(resumoEl,
      el("p", { class: "total-geral" }, "Total do orçamento ", el("strong", { class: "num" }, brl(r.total))),
      r.semValor ? el("p", { class: "nota" }, `${r.semValor} item(ns) ainda sem valor: o total está incompleto.`) : null,
      barra,
      porEtapa.length ? el("ul", { class: "pct-etapas" }, porEtapa) : null);
  }

  function linhaDoItem(l) {
    const total = el("output", { class: "total-item num" });
    const aviso = el("span", { class: "chip-confirmar", hidden: true }, "valor a confirmar");
    const atualizar = () => {
      const t = totalDoItem(l);
      total.textContent = t == null ? "—" : brl(t);
      aviso.hidden = !valorAConfirmar(l);
    };
    atualizadores.push(atualizar);
    atualizar();
    const mudou = () => { atualizar(); desenharResumo(); desenharSubtotais(); };
    const item = campo(c, l, "item", { rotulo: "Item", maxlength: 200, classe: "col-item", onInput: mudou });
    return el("li", { class: "linha-item", "data-id": l.id },
      item.no,
      campo(c, l, "rubrica", { rotulo: "Rubrica", maxlength: 120, placeholder: "Ex.: Equipe", classe: "col-rubrica" }).no,
      campo(c, l, "unidade", { rotulo: "Unidade", maxlength: 40, classe: "col-unidade" }).no,
      campo(c, l, "quantidade", { rotulo: "Qtd", tipo: "num", classe: "col-qtd", onInput: mudou }).no,
      campo(c, l, "valor_unitario", { rotulo: "Valor unitário (R$)", tipo: "num", placeholder: "a confirmar", classe: "col-valor", onInput: mudou }).no,
      el("div", { class: "campo-tab col-total" }, el("span", { class: "rotulo" }, "Total"), total, aviso),
      campo(c, l, "fonte_preco", { rotulo: "Fonte do preço", maxlength: 300, placeholder: "fornecedor, sindicato, mercado…", classe: "col-fonte", onInput: mudou }).no,
      campo(c, l, "justificativa", { rotulo: "Justificativa", maxlength: 600, classe: "col-justificativa" }).no,
      botao("Remover", () => { c.remover(l); desenhar(); }, "btn btn-discreto remover", { "aria-label": `Remover o item ${l.item || "sem nome"}` }));
  }

  const subtotais = new Map();
  function desenharSubtotais() {
    const r = resumoOrcamento(c.linhas);
    for (const [e, no] of subtotais) no.textContent = `${brl(r.porEtapa[e].total)} · ${pct(r.porEtapa[e].percentual)}`;
  }

  function desenhar() {
    atualizadores.length = 0;
    subtotais.clear();
    const grupos = ETAPAS.map((e) => {
      const itens = c.linhas.filter((l) => l.etapa === e);
      if (!itens.length && etapaEmFoco && e !== etapaEmFoco && !(maximos()[e] != null)) return null;
      const sub = el("span", { class: "subtotal num" });
      subtotais.set(e, sub);
      return el("section", { class: "grupo-etapa", "aria-labelledby": `etapa-${e}` },
        el("h4", { id: `etapa-${e}` }, ROTULOS_ETAPA[e], " ", sub),
        itens.length ? el("ul", { class: "linhas" }, itens.map(linhaDoItem)) : el("p", { class: "nota" }, "Nenhum item nesta etapa."),
        botao(`Adicionar item em ${ROTULOS_ETAPA[e]}`, () => { const l = c.adicionar({ etapa: e, unidade: "unidade", quantidade: 1 }); desenhar(); raiz.querySelector(`[data-id="${l.id}"] input`)?.focus(); }, "btn"));
    }).filter(Boolean);
    preencher(corpo, grupos);
    desenharResumo();
    desenharSubtotais();
  }
  c.aoAtualizar(() => { /* o resumo é redesenhado pelos próprios campos; aqui só o status */ });
  desenhar();
  preencher(raiz, resumoEl, corpo, barraDeStatus(c));
  return { no: raiz, redesenhar: desenhar };
}
