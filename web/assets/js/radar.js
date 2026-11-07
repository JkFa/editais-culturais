// "Costuma abrir em breve": editais recorrentes hoje fechados, nunca contados como abertos.
import { el } from "./formatos.js";
import { mesAtualBrasilia, mesesAteJanela } from "./prazos.js";

export function ordenarRadar(itens, mes = mesAtualBrasilia()) {
  const distancia = (r) => mesesAteJanela(r.janela_mes_inicio, r.janela_mes_fim, mes) ?? 99;
  return [...itens].sort((a, b) => distancia(a) - distancia(b));
}

export function renderRadar(secao, lista, itens) {
  if (!itens?.length) {
    secao.hidden = true;
    return;
  }
  secao.hidden = false;
  lista.replaceChildren(
    ...ordenarRadar(itens).map((r) =>
      el(
        "li",
        {},
        el(
          "div",
          {},
          el(
            "div",
            { class: "titulo-radar" },
            r.url_referencia
              ? el("a", { href: r.url_referencia, target: "_blank", rel: "noopener noreferrer" }, r.titulo)
              : r.titulo,
          ),
          el("div", { class: "detalhe" }, r.orgao_fomento),
        ),
        el(
          "div",
          {},
          r.janela_habitual ? el("div", { class: "janela" }, `Costuma abrir: ${r.janela_habitual}`) : null,
          r.ultima_edicao ? el("div", { class: "detalhe" }, `Sobre a última edição: ${r.ultima_edicao}`) : null,
        ),
      ),
    ),
  );
}
