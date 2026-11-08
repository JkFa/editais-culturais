// Página inicial: números do dia, prazos da semana, índice por linguagem e perfil, radar e visitantes.
// Tudo vem dos mesmos dados do explorador (snapshot primeiro, Supabase em seguida); nada é inventado.
import { badgePrazo } from "./cards.js";
import { renderVisitantes } from "./cartograma.js";
import { config, iniciarPagina, preencherUltimaColeta } from "./comum.js";
import { carregarProgressivo, carregarVisitas } from "./dados.js";
import { LINGUAGENS, PERFIS, contadores, filtrar } from "./filtros.js";
import { el, formatarMoeda, formatarMoedaCompacta, localDoEdital, plural } from "./formatos.js";
import { AMBITOS, ehInternacional } from "./internacional.js";
import { diasRestantes, encerrado } from "./prazos.js";
import { renderRadar } from "./radar.js";

const $ = (seletor) => document.querySelector(seletor);
const LIMITE_SEMANA = 6;
const fmt = new Intl.NumberFormat("pt-BR");

// Atalhos de perfil da home: "PJ" e "OSC" mapeiam os dois tipos de pessoa jurídica.
const ATALHOS_PERFIL = [
  { id: "PF", rotulo: "Pessoa física (PF)" },
  { id: "MEI", rotulo: "MEI" },
  { id: "PJ_COM_FINS", rotulo: "Empresa (PJ)" },
  { id: "PJ_SEM_FINS", rotulo: "Associação ou instituto (OSC)" },
  { id: "COLETIVO", rotulo: "Coletivo sem CNPJ" },
];

function linkExplorador(parametros) {
  const busca = new URLSearchParams(parametros).toString();
  return busca ? `editais/?${busca}` : "editais/";
}

function renderNumeros(editais) {
  const c = contadores(editais);
  // Os números do Brasil não mudam com as oportunidades de fora; elas têm contador próprio.
  const fontes = new Set(editais.filter((e) => !ehInternacional(e)).map((e) => e.fonte)).size;
  $('[data-numero="abertos"]').textContent = fmt.format(c.abertos);
  $('[data-numero="internacionais"]').textContent = fmt.format(c.internacionaisAbertos);
  $('[data-numero="encerrando"]').textContent = fmt.format(c.encerrandoEm7Dias);
  const valor = $('[data-numero="valor"]');
  valor.textContent = c.comValorInformado ? formatarMoedaCompacta(c.somaValores) : "—";
  valor.title = c.comValorInformado ? formatarMoeda(c.somaValores) : "";
  // Mesma regra do explorador: nunca somar valor nulo como zero sem dizer.
  $('[data-numero-rotulo="valor"]').textContent = c.comValorInformado
    ? `em ${plural(c.comValorInformado, "edital", "editais")} com valor informado`
    : "nenhum edital informa o valor total no resumo";
  $('[data-numero="fontes"]').textContent = fmt.format(fontes);
}

function renderSemana(editais) {
  const lista = $("#semana");
  const mais = $("#semana-mais");
  const daSemana = editais
    .filter((e) => !ehInternacional(e) && !encerrado(e) && diasRestantes(e.inscricao_fim) <= 7)
    .sort((a, b) => new Date(a.inscricao_fim) - new Date(b.inscricao_fim));
  if (!daSemana.length) {
    lista.replaceChildren(el("li", { class: "suave" }, "Nenhum edital encerra nos próximos 7 dias"));
    mais.hidden = true;
    return;
  }
  lista.replaceChildren(
    ...daSemana.slice(0, LIMITE_SEMANA).map((e) => {
      const local = localDoEdital(e);
      return el(
        "li",
        {},
        el(
          "a",
          { class: "item-semana", href: linkExplorador({ edital: e.chave }) },
          badgePrazo(e),
          el("span", { class: "item-titulo" }, e.titulo),
          el("span", { class: "item-orgao" }, e.orgao_fomento, local ? ` · ${local}` : ""),
        ),
      );
    }),
  );
  mais.hidden = daSemana.length <= LIMITE_SEMANA;
  if (!mais.hidden) {
    mais.replaceChildren(
      el("a", { class: "link-seta", href: "editais/" }, `Ver os ${daSemana.length} editais que encerram em 7 dias`),
    );
  }
}

let escopoLinguagem = "brasil";

function renderLinguagens(editais) {
  const abertos = editais.filter(
    (e) =>
      !encerrado(e) &&
      (escopoLinguagem === "todos" || (escopoLinguagem === "internacional") === ehInternacional(e)),
  );
  $("#escopo-linguagem").replaceChildren(
    ...AMBITOS.map((a) =>
      el(
        "button",
        {
          type: "button",
          class: "chip",
          "aria-pressed": String(escopoLinguagem === a.id),
          onclick: () => {
            escopoLinguagem = a.id;
            renderLinguagens(editais);
          },
        },
        a.rotulo,
      ),
    ),
  );
  const [um, varios] = escopoLinguagem === "internacional" ? [" oportunidade aberta", " oportunidades abertas"] : [" edital aberto", " editais abertos"];
  $("#indice-linguagem").replaceChildren(
    ...LINGUAGENS.map((nome) => {
      const total = abertos.filter((e) => e.linguagens?.includes(nome)).length;
      // O texto visível já é o nome acessível ("Música 8 editais abertos"): sem aria-label divergente.
      const conteudo = [
        el("span", { class: "indice-nome" }, nome),
        el("span", { class: "indice-n num" }, String(total), el("span", { class: "sr-only" }, total === 1 ? um : varios)),
      ];
      if (!total) return el("li", { class: "indice-vazio" }, conteudo);
      const params = escopoLinguagem === "brasil" ? { ling: nome } : { ling: nome, amb: escopoLinguagem };
      return el("li", {}, el("a", { href: linkExplorador(params) }, conteudo));
    }),
  );
}

function renderPerfis(editais) {
  const estadoBase = { q: "", perfil: "", linguagens: [], abrangencia: "", uf: "", apenasAbertas: true, ambito: "brasil" };
  $("#perfis").replaceChildren(
    ...ATALHOS_PERFIL.map(({ id, rotulo }) => {
      // Conta os que aceitam o perfil e os que não informam quem pode participar.
      const total = filtrar(editais, { ...estadoBase, perfil: id }).length;
      return el(
        "li",
        {},
        el(
          "a",
          { class: "perfil", href: linkExplorador({ perfil: id }) },
          el("span", { class: "perfil-nome" }, rotulo),
          el("span", { class: "perfil-n num" }, `${total} editais`),
        ),
      );
    }),
  );
}

function aplicarDados(dados) {
  renderNumeros(dados.editais);
  renderSemana(dados.editais);
  renderLinguagens(dados.editais);
  renderPerfis(dados.editais);
  renderRadar($("#radar"), $("#radar-lista"), dados.radar);
  preencherUltimaColeta(dados.geradoEm);
  document.documentElement.dataset.pronto = "1";
  document.documentElement.dataset.origem = dados.origem;
}

async function iniciar() {
  iniciarPagina("home");
  // Visitantes: independe dos editais; sem Supabase o bloco simplesmente não aparece.
  carregarVisitas(config).then((resumo) => renderVisitantes($("#visitantes"), resumo));
  try {
    await carregarProgressivo(config, aplicarDados);
  } catch (erro) {
    console.error(erro);
    $("#semana").replaceChildren(
      el("li", { class: "suave" }, "Não consegui carregar os editais agora. Tente de novo em instantes."),
    );
  }
}

iniciar();
