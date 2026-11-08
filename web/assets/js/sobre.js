// Página "sobre" e 404: tema, contagem de visita, data da última coleta e (em "sobre") a lista de fontes.
import { config, iniciarPagina, preencherUltimaColeta } from "./comum.js";
import { carregarProgressivo } from "./dados.js";
import { el, formatarData } from "./formatos.js";
import { iniciarTema } from "./tema.js";

// A 404 também carrega este módulo, mas não conta como visita.
if (location.pathname.includes("/sobre")) iniciarPagina("sobre");
else iniciarTema();

const GRUPOS = [
  ["oficial", "Oficiais"],
  ["plataforma", "Plataformas"],
  ["descoberta", "Descoberta (só links; os dados vêm da fonte oficial)"],
];
const METODOS = { api: "API pública", html: "leitura de HTML", rss: "RSS", playwright: "navegador", vigia: "vigia de página" };
const ROTULO_STATUS = {
  ok: ["funcionando", "ok"],
  sem_dados: ["sem dados hoje", "ok"],
  erro: ["com erro", "problema"],
  bloqueada_em_ci: ["bloqueia o robô", "problema"],
  bloqueada_por_robots: ["robots.txt não permite", "problema"],
  conteudo_insuficiente: ["página sem texto para o robô", "problema"],
  desativada: ["desativada", "inativa"],
};

function linhaDaFonte(f) {
  const [rotulo, classe] = ROTULO_STATUS[f.status] ?? [f.status, "inativa"];
  const detalhes = [METODOS[f.metodo] ?? f.metodo];
  if (f.ultimo_sucesso && f.ativa) detalhes.push(`última coleta boa em ${formatarData(f.ultimo_sucesso)}`);
  if (f.termos_conferidos_em) detalhes.push(`termos conferidos em ${formatarData(`${f.termos_conferidos_em}T12:00:00-03:00`)}`);
  return el(
    "li",
    { class: "fonte-item" },
    el("a", { href: f.url, rel: "noopener" }, f.nome),
    el("span", { class: `saude ${classe}` }, rotulo),
    el("span", { class: "meta" }, detalhes.join(" · "), f.motivo ? ` · ${f.motivo}` : "", f.erro && f.ativa ? ` · ${f.erro}` : ""),
  );
}

function renderFontes(fontes) {
  const alvo = document.querySelector("#fontes-lista");
  if (!alvo) return;
  const ativas = fontes.filter((f) => f.ativa).length;
  document.querySelector("#fontes-resumo").textContent = `${ativas} fontes ativas e ${fontes.length - ativas} avaliadas e desativadas, com o motivo. A saúde vem da última coleta.`;
  const blocos = [];
  for (const [tipo, titulo] of GRUPOS) {
    const itens = fontes.filter((f) => f.tipo === tipo && !f.fonte.startsWith("mapas:"));
    const mapas = tipo === "plataforma" ? fontes.filter((f) => f.fonte.startsWith("mapas:")) : [];
    const todas = [...itens, ...mapas].sort((a, b) => Number(b.ativa) - Number(a.ativa) || a.nome.localeCompare(b.nome, "pt-BR"));
    if (!todas.length) continue;
    blocos.push(el("h3", {}, titulo), el("ul", {}, todas.map(linhaDaFonte)));
  }
  alvo.replaceChildren(...blocos);
}

// ── Cobertura ────────────────────────────────────────────────────────────────────────────────
const fmtNota = (n) => Number(n).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
const SITUACAO_FONTE = { 1: "tem", 0.5: "parcial", 0: "não tem" };

export function renderCobertura(c) {
  const resumo = document.querySelector("#cobertura-resumo");
  const nota = document.querySelector("#cobertura-nota");
  if (!resumo || !nota) return;
  if (!c) {
    resumo.textContent = "O placar de cobertura ainda não foi calculado.";
    return;
  }
  resumo.textContent = `Quanto o portal enxerga dos editais do Brasil, em medidas que se conferem. Calculado em ${formatarData(c.gerado_em)}.`;
  nota.hidden = false;
  nota.replaceChildren(
    el("p", { class: "nota-grande" }, el("strong", {}, fmtNota(c.nota)), " de 10"),
    el("p", { class: "suave" }, c.dias_seguidos_9 ? `Nota ≥ 9 há ${c.dias_seguidos_9} dia(s) seguido(s).` : "Meta: nota 9 ou mais por 14 dias seguidos e recall auditado de 90%."),
  );
  document.querySelector("#cobertura-metricas").replaceChildren(
    ...c.metricas.map((m) =>
      el("li", { class: "cobertura-item" },
        el("div", { class: "cobertura-titulo" }, el("span", {}, m.nome), el("span", { class: "meta" }, `${fmtNota(m.pontos)} de ${fmtNota(m.peso)}`)),
        el("div", { class: "barra", role: "img", "aria-label": `${Math.round(m.fracao * 100)}% da meta` }, el("span", { style: `width:${Math.round(m.fracao * 100)}%` })),
        el("span", { class: "meta" }, `${m.texto}. Meta: ${m.meta}.`),
      )),
  );
  const tabela = document.querySelector("#cobertura-uf");
  const cab = el("thead", {}, el("tr", {}, ["UF", "Abertos", "Fonte do estado", "Fonte da capital", "Fontes ativas"].map((t) => el("th", { scope: "col" }, t))));
  const corpo = el("tbody", {}, c.por_uf.map((u) =>
    el("tr", {}, el("th", { scope: "row" }, `${u.uf} · ${u.nome}`), el("td", {}, String(u.abertos)),
      el("td", {}, SITUACAO_FONTE[u.fonte_estadual]), el("td", {}, `${u.capital}: ${SITUACAO_FONTE[u.capital_coberta]}`),
      el("td", {}, u.fontes.join(", ") || "—"))));
  tabela.replaceChildren(tabela.querySelector("caption"), cab, corpo);
  document.querySelector("#cobertura-uf-bloco").hidden = false;
}

fetch(new URL("../../data/cobertura.json", import.meta.url))
  .then((r) => (r.ok ? r.json() : null))
  .catch(() => null)
  .then(renderCobertura);

// O snapshot basta: ele leva o catálogo com a saúde de cada fonte (a mesma informação de `fontes_status`).
carregarProgressivo({ ...config, supabaseUrl: "", supabaseAnonKey: "" }, (d) => {
  preencherUltimaColeta(d.geradoEm);
  renderFontes(d.fontes ?? []);
}).catch(() => preencherUltimaColeta(null));

document.documentElement.dataset.pronto = "1"; // sinal para testes automatizados
