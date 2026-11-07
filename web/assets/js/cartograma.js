// Bloco "Visitantes": frase-resumo, cartograma do Brasil em grade CSS (um quadrado por UF),
// cidades que mais visitaram e a mesma informação em tabela acessível.
import { el, plural } from "./formatos.js";

// Posição (coluna, linha) de cada UF, aproximando a geografia do país.
export const POSICAO_UF = {
  RR: [3, 1], AP: [5, 1],
  AM: [2, 2], PA: [4, 2], MA: [5, 2], CE: [6, 2], RN: [7, 2],
  AC: [1, 3], RO: [2, 3], MT: [3, 3], TO: [4, 3], PI: [5, 3], PE: [6, 3], PB: [7, 3],
  MS: [3, 4], GO: [4, 4], DF: [5, 4], BA: [6, 4], AL: [7, 4],
  SP: [4, 5], MG: [5, 5], ES: [6, 5], SE: [7, 5],
  PR: [4, 6], RJ: [5, 6],
  SC: [4, 7],
  RS: [4, 8],
}; // fmt: skip

const NIVEIS = 5;
const fmt = new Intl.NumberFormat("pt-BR");

/** Nível 0..5 por UF. Escala por raiz quadrada, para um estado dominante não apagar os demais. */
export function niveisPorUf(porUf) {
  const maximo = Math.max(0, ...porUf.map((x) => x.total));
  const niveis = {};
  for (const uf of Object.keys(POSICAO_UF)) niveis[uf] = 0;
  for (const { uf, total } of porUf) {
    if (!(uf in niveis) || total <= 0 || maximo <= 0) continue;
    niveis[uf] = Math.max(1, Math.ceil(Math.sqrt(total / maximo) * NIVEIS));
  }
  return niveis;
}

export function fraseDeVisitas(resumo) {
  const estados = resumo.por_uf.filter((x) => x.total > 0).length;
  const total = `${fmt.format(resumo.total_geral)} ${resumo.total_geral === 1 ? "visita" : "visitas"}`;
  return estados
    ? `${total} · pessoas de ${plural(estados, "estado", "estados")} nos últimos 30 dias`
    : `${total} desde o início`;
}

function cartograma(porUf) {
  const niveis = niveisPorUf(porUf);
  const totais = Object.fromEntries(porUf.map((x) => [x.uf, x.total]));
  const grade = el("ol", { class: "cartograma", "aria-label": "Visitas por estado nos últimos 30 dias" });
  for (const [uf, [coluna, linha]] of Object.entries(POSICAO_UF)) {
    const total = totais[uf] ?? 0;
    const texto = `${uf}: ${plural(total, "visita", "visitas")}`;
    grade.append(
      el(
        "li",
        { class: "cg-celula", "data-nivel": String(niveis[uf]), style: `grid-column:${coluna};grid-row:${linha}` },
        el("button", { type: "button", class: "cg-botao", title: texto, "aria-label": texto }, uf),
      ),
    );
  }
  return grade;
}

function legenda() {
  return el(
    "div",
    { class: "cg-legenda", "aria-hidden": "true" },
    el("span", {}, "menos"),
    ...Array.from({ length: NIVEIS + 1 }, (_, n) => el("i", { class: "cg-amostra", "data-nivel": String(n) })),
    el("span", {}, "mais"),
  );
}

function tabelaAcessivel(resumo) {
  const linhasUf = [...resumo.por_uf].sort((a, b) => b.total - a.total || a.uf.localeCompare(b.uf));
  return el(
    "details",
    { class: "cg-tabela" },
    el("summary", {}, "Ver como tabela"),
    el(
      "table",
      {},
      el("caption", {}, "Visitas por estado, últimos 30 dias"),
      el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Estado"), el("th", { scope: "col" }, "Visitas"))),
      el(
        "tbody",
        {},
        linhasUf.length
          ? linhasUf.map((x) => el("tr", {}, el("th", { scope: "row" }, x.uf), el("td", { class: "num" }, fmt.format(x.total))))
          : el("tr", {}, el("td", { colspan: "2" }, "Ainda sem visitas com localização.")),
      ),
    ),
  );
}

function listaDeCidades(resumo) {
  const itens = resumo.top_cidades.map((c) =>
    el("li", {}, el("span", {}, c.cidade, c.uf ? `/${c.uf}` : ""), el("span", { class: "num" }, fmt.format(c.total))),
  );
  if (resumo.outras_cidades > 0) {
    itens.push(el("li", { class: "suave" }, el("span", {}, "outras cidades"), el("span", { class: "num" }, fmt.format(resumo.outras_cidades))));
  }
  if (!itens.length) return el("p", { class: "suave" }, "Ainda não há cidade com 5 visitas ou mais no período.");
  return el("ol", { class: "cg-cidades", "aria-label": "Cidades que mais visitaram" }, itens);
}

/** Preenche `alvo` com o bloco de visitantes. Sem resumo (Supabase fora do ar), esconde o bloco. */
export function renderVisitantes(alvo, resumo) {
  if (!resumo || typeof resumo.total_geral !== "number") {
    alvo.hidden = true;
    return;
  }
  alvo.hidden = false;
  alvo.querySelector("[data-visitas-frase]").textContent = fraseDeVisitas(resumo);
  alvo.querySelector("[data-visitas-mapa]").replaceChildren(cartograma(resumo.por_uf), legenda());
  alvo.querySelector("[data-visitas-cidades]").replaceChildren(listaDeCidades(resumo));
  alvo.querySelector("[data-visitas-tabela]").replaceChildren(tabelaAcessivel(resumo));
}
