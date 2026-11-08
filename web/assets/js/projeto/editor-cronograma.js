// Editor do cronograma: grade atividade × mês (mês 1…N, com N = prazo de execução do edital ou 12).
// As células são botões (teclado e leitor de tela). No celular a grade some e ficam dois campos
// numéricos por atividade (mês inicial e final).
import { el } from "../formatos.js";
import { barraDeStatus, botao, campo, preencher } from "./editor-base.js";
import { ETAPAS, ROTULOS_ETAPA } from "./tabelas.mjs";

/** Clique numa célula: estende o intervalo, ou o encolhe quando o mês é a ponta dele. */
export function alternarMes(inicio, fim, mes) {
  const a = Number(inicio);
  const b = Number(fim);
  if (!Number.isInteger(a) || !Number.isInteger(b) || a < 1 || b < a) return [mes, mes];
  if (mes < a) return [mes, b];
  if (mes > b) return [a, mes];
  if (a === b) return [a, b]; // um mês só: clicar nele não apaga a atividade
  if (mes === a) return [a + 1, b];
  if (mes === b) return [a, b - 1];
  return [a, mes]; // clique no meio: o fim passa a ser este mês
}

export function criarEditorCronograma({ c, criterios }) {
  const raiz = el("div", { class: "editor editor-cronograma" });
  const corpo = el("div", { class: "cronograma-corpo" });
  const prazo = Number(criterios?.prazo_execucao_meses) || 0;
  let meses = Math.max(prazo || 12, ...c.linhas.map((l) => Number(l.mes_fim) || 0));

  function desenhar() {
    const cab = el("div", { class: "cron-cab", "aria-hidden": "true" },
      el("span", { class: "cron-ativ" }, "Atividade"),
      el("span", { class: "grade-meses" }, Array.from({ length: meses }, (_, i) => el("span", { class: `mes${prazo && i + 1 > prazo ? " alem" : ""}` }, String(i + 1)))));
    const linhas = c.linhas.map((l) => {
      const celulas = el("div", { class: "grade-meses", role: "group", "aria-label": `Meses da atividade ${l.atividade || "sem nome"}` });
      const pintar = () => {
        preencher(celulas, Array.from({ length: meses }, (_, i) => {
          const m = i + 1;
          const ativo = Number(l.mes_inicio) <= m && m <= Number(l.mes_fim);
          return el("button", {
            type: "button",
            class: `celula${ativo ? " marcada" : ""}${prazo && m > prazo ? " alem" : ""}`,
            "aria-pressed": String(ativo),
            "aria-label": `Mês ${m}${prazo && m > prazo ? " (depois do prazo do edital)" : ""}`,
            onclick: () => {
              const [a, b] = alternarMes(l.mes_inicio, l.mes_fim, m);
              c.alterar(l, "mes_inicio", a);
              c.alterar(l, "mes_fim", b);
              c.tocar(l);
              pintar();
              nums.querySelectorAll("input").forEach((x, k) => (x.value = String(k ? b : a)));
            },
          });
        }));
      };
      const nums = el("div", { class: "meses-numericos" },
        campo(c, l, "mes_inicio", { rotulo: "Mês inicial", tipo: "num", classe: "col-mes", onInput: pintar }).no,
        campo(c, l, "mes_fim", { rotulo: "Mês final", tipo: "num", classe: "col-mes", onInput: pintar }).no);
      pintar();
      return el("li", { class: "linha-cron", "data-id": l.id },
        campo(c, l, "atividade", { rotulo: "Atividade", maxlength: 200, classe: "cron-ativ" }).no,
        campo(c, l, "etapa", { rotulo: "Etapa", opcoes: ETAPAS.map((e) => [e, ROTULOS_ETAPA[e]]), classe: "cron-etapa" }).no,
        celulas, nums,
        botao("Remover", () => { c.remover(l); desenhar(); }, "btn btn-discreto remover", { "aria-label": `Remover a atividade ${l.atividade || "sem nome"}` }));
    });
    preencher(corpo,
      prazo ? el("p", { class: "nota" }, `O edital permite até ${prazo} meses de execução; meses depois disso aparecem em destaque.`) : el("p", { class: "nota" }, "Mês 1 = primeiro mês de execução do projeto."),
      c.linhas.length ? el("div", { class: "cron-grade" }, cab, el("ul", { class: "linhas" }, linhas)) : el("p", { class: "nota" }, "Nenhuma atividade ainda."),
      el("div", { class: "acoes" },
        botao("Adicionar atividade", () => { const l = c.adicionar({ etapa: "producao", mes_inicio: 1, mes_fim: 1 }); desenhar(); raiz.querySelector(`[data-id="${l.id}"] input`)?.focus(); }, "btn"),
        meses < 120 ? botao("Mostrar mais um mês", () => { meses += 1; desenhar(); }) : null));
  }
  desenhar();
  preencher(raiz, corpo, barraDeStatus(c));
  return { no: raiz, redesenhar: desenhar };
}
