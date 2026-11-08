// Painel "Pendências do projeto": roda as checagens cruzadas (sem IA) e mostra cada problema com um
// link para a seção onde se resolve. Regras que não puderam rodar aparecem como "não verificado".
import { el } from "../formatos.js";
import { executarChecagens, ordenarPendencias } from "./checagens.mjs";
import { botao, preencher } from "./editor-base.js";

const ROTULO_NIVEL = { erro: "Problema", aviso: "Atenção", info: "Dica", nao_verificado: "Não verificado" };

export function criarPainelPendencias({ coletar, titulos, irPara }) {
  const corpo = el("div", {});
  const resumo = el("span", { class: "resumo-pendencias" });
  // No celular o painel começa fechado (senão empurra a tabela para baixo); no desktop, aberto.
  const largo = typeof matchMedia === "function" && matchMedia("(min-width: 64rem)").matches;
  const raiz = el("details", { class: "pendencias", open: largo || null },
    el("summary", {}, "Pendências do projeto ", resumo),
    corpo);

  function atualizar() {
    const lista = ordenarPendencias(executarChecagens(coletar()));
    const reais = lista.filter((p) => p.nivel !== "nao_verificado");
    const erros = lista.filter((p) => p.nivel === "erro").length;
    resumo.textContent = reais.length ? `· ${reais.length}${erros ? ` (${erros} contra o edital)` : ""}` : "· nenhuma";
    resumo.className = `resumo-pendencias${erros ? " tem-erro" : ""}`;
    if (!lista.length) {
      preencher(corpo, el("p", { class: "nota" }, "Nenhuma pendência encontrada nas regras automáticas. Elas conferem totais, limites do edital, metas, cronograma, equipe e acessibilidade; não substituem a leitura do edital."));
      return;
    }
    preencher(corpo,
      el("ul", { class: "lista-pendencias" }, lista.map((p) =>
        el("li", { class: `pendencia nv-${p.nivel}` },
          el("strong", {}, `${ROTULO_NIVEL[p.nivel]}: `), p.mensagem, " ",
          botao(`Ir para ${titulos[p.secao] ?? p.secao}`, () => irPara(p.secao), "btn btn-discreto", { "aria-label": `Ir para a seção ${titulos[p.secao] ?? p.secao}: ${p.mensagem}` })))));
  }
  atualizar();
  return { no: raiz, atualizar };
}
