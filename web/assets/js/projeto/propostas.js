// Linhas PROPOSTAS pelo assistente para uma tabela. Nada é gravado até a pessoa aceitar: cada linha pode
// ser aceita, editada (entra na tabela e o foco vai para ela) ou descartada. Valor sem número dito pela
// pessoa chega como "valor a confirmar".
import { el } from "../formatos.js";
import { brl } from "./editor-orcamento.js";
import { botao, preencher } from "./editor-base.js";
import { ROTULOS_ETAPA, lerNumero, semAcento } from "./tabelas.mjs";

const chaveDe = (tabela, l) => {
  const base = { orcamento_itens: [l.etapa, l.item], cronograma_itens: [l.atividade, l.mes_inicio], midia_itens: [l.peca, l.canal], equipe_membros: [l.funcao, l.nome_artistico_ou_funcao] }[tabela] ?? [];
  return semAcento(base.join("|"));
};

export function resumoDaLinha(tabela, l) {
  switch (tabela) {
    case "orcamento_itens": {
      const v = lerNumero(l.valor_unitario);
      const preco = v == null || Number.isNaN(v) ? "valor a confirmar" : `${l.quantidade ?? 1} × ${brl(v)}`;
      return `${l.item} · ${ROTULOS_ETAPA[l.etapa] ?? l.etapa} · ${preco}`;
    }
    case "cronograma_itens":
      return `${l.atividade} · ${l.mes_inicio === l.mes_fim ? `mês ${l.mes_inicio}` : `meses ${l.mes_inicio} a ${l.mes_fim}`}`;
    case "midia_itens":
      return `${l.peca}${l.canal ? ` · ${l.canal}` : ""}${l.periodo ? ` · ${l.periodo}` : ""}${l.custo != null ? ` · ${brl(l.custo)}` : ""}`;
    case "equipe_membros":
      return `${l.funcao}${l.nome_artistico_ou_funcao && l.nome_artistico_ou_funcao !== l.funcao ? ` — ${l.nome_artistico_ou_funcao}` : ""}${l.local ? ` · ${l.local}` : ""}`;
    default:
      return "";
  }
}

const lerDispensadas = (k) => {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(k) ?? "[]"));
  } catch {
    return new Set();
  }
};
const gravarDispensadas = (k, s) => {
  try {
    sessionStorage.setItem(k, JSON.stringify([...s]));
  } catch {
    /* sem armazenamento: a dispensa vale só enquanto a tela estiver aberta */
  }
};

/**
 * @param {{c: object, tabela: string, proposta: {tabela: string, linhas: object[]}|null, chaveSessao: string, aoAceitar: (linha: object, editar: boolean) => void}} o
 */
export function criarPropostas({ c, tabela, proposta, chaveSessao, aoAceitar }) {
  const raiz = el("section", { class: "propostas", "aria-labelledby": "titulo-propostas" });
  const dispensadas = lerDispensadas(chaveSessao);
  const jaNaTabela = () => new Set(c.linhas.map((l) => chaveDe(tabela, l)));

  function desenhar() {
    if (!proposta || proposta.tabela !== tabela) return preencher(raiz);
    const existentes = jaNaTabela();
    const pendentes = proposta.linhas.filter((l) => !existentes.has(chaveDe(tabela, l)) && !dispensadas.has(chaveDe(tabela, l)));
    if (!pendentes.length) return preencher(raiz);
    const aceitar = (l, editar) => {
      dispensadas.add(chaveDe(tabela, l));
      gravarDispensadas(chaveSessao, dispensadas);
      aoAceitar(l, editar);
      desenhar();
    };
    preencher(raiz,
      el("h3", { id: "titulo-propostas" }, `Linhas sugeridas pelo assistente (${pendentes.length})`),
      el("p", { class: "nota" }, "São sugestões: nada entra na tabela até você aceitar. Confira cada valor; o assistente não define preços."),
      el("ul", { class: "lista-propostas" }, pendentes.map((l) =>
        el("li", { class: "proposta" },
          el("span", {}, resumoDaLinha(tabela, l)),
          el("span", { class: "acoes" },
            botao("Aceitar", () => aceitar(l, false), "btn", { "aria-label": `Aceitar: ${resumoDaLinha(tabela, l)}` }),
            botao("Editar", () => aceitar(l, true), "btn", { "aria-label": `Aceitar e editar: ${resumoDaLinha(tabela, l)}` }),
            botao("Descartar", () => { dispensadas.add(chaveDe(tabela, l)); gravarDispensadas(chaveSessao, dispensadas); desenhar(); }, "btn btn-discreto", { "aria-label": `Descartar: ${resumoDaLinha(tabela, l)}` }))))),
      el("div", { class: "acoes" },
        botao("Aceitar todas", () => pendentes.forEach((l) => aceitar(l, false)), "btn btn-primario"),
        botao("Descartar todas", () => { pendentes.forEach((l) => dispensadas.add(chaveDe(tabela, l))); gravarDispensadas(chaveSessao, dispensadas); desenhar(); }, "btn")));
  }
  c.aoAtualizar(() => {});
  desenhar();
  return { no: raiz, redesenhar: desenhar };
}
