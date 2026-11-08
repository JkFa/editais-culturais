// Orquestra a página: dados, filtros, URL, lista, painel, favoritos e radar.
import { atualizarBadges, criarCard, criarCardRemovido } from "./cards.js";
import { config, iniciarPagina, preencherUltimaColeta } from "./comum.js";
import { carregarProgressivo } from "./dados.js";
import { Painel, copiarTexto } from "./drawer.js";
import {
  Favoritos,
  baixarArquivo,
  gerarCsv,
  gerarIcs,
  gerarTextoWhatsapp,
  resolverFavoritos,
} from "./favoritos.js";
import {
  LINGUAGENS,
  ORDENS,
  PERFIS,
  TIPOS_APOIO,
  UFS,
  contadores,
  filtrar,
  filtrosAtivos,
  ordenar,
} from "./filtros.js";
import { el, formatarDataHora, formatarMoeda, formatarMoedaCompacta, plural } from "./formatos.js";
import { AMBITOS, ehInternacional, nomeDoPais, paisesDosEditais } from "./internacional.js";
import { renderRadar } from "./radar.js";
import { empilharPainel, gravarEstado, lerEstado, urlDoPortal } from "./url-state.js";

const $ = (seletor) => document.querySelector(seletor);

let dados = null;
let estado = lerEstado().estado;
let editalAbertoChave = lerEstado().edital;
let chavesServidor = null;
let temporizadorBusca = null;

const painel = new Painel($("#painel-raiz"), $("#app-raiz"));
const favoritos = new Favoritos();

// ---------------------------------------------------------------- avisos

let temporizadorAviso = null;
function anunciar(mensagem) {
  const regiao = $("#aviso-live");
  regiao.textContent = mensagem;
  clearTimeout(temporizadorAviso);
  temporizadorAviso = setTimeout(() => (regiao.textContent = ""), 5000);
}

// ---------------------------------------------------------------- filtros (UI)

function criarChip(rotulo, pressionado, aoClicar, extra = {}) {
  return el("button", { type: "button", class: "chip", "aria-pressed": String(pressionado), onclick: aoClicar, ...extra }, rotulo);
}

function criarPergunta(id, rotulo, opcoes, valor, aoMudar) {
  return el(
    "label",
    { class: "pergunta-intl", for: id },
    el("span", {}, rotulo),
    el(
      "select",
      { class: "chip", id, onchange: (e) => aoMudar(e.target.value) },
      opcoes.map(([v, texto]) => el("option", { value: v, selected: v === valor }, texto)),
    ),
  );
}

function renderAmbitoEPerguntas() {
  $("#chips-ambito").replaceChildren(
    ...AMBITOS.map((a) =>
      criarChip(a.rotulo, estado.ambito === a.id, () => {
        estado.ambito = a.id;
        aoMudarFiltro();
      }),
    ),
  );
  const fora = estado.ambito !== "brasil";
  const soFora = estado.ambito === "internacional";
  $("#grupo-intl").hidden = !fora;
  // Perfil, onde (UF/nacional) e tipo de apoio são do Brasil: somem quando só há oportunidades de fora.
  for (const id of ["grupo-perfil", "grupo-abrangencia", "grupo-apoio"]) $(`#${id}`).hidden = soFora;
  if (!fora) return;
  const paises = paisesDosEditais(dados?.editais ?? []);
  $("#perguntas-intl").replaceChildren(
    criarPergunta(
      "pergunta-nacionalidade",
      "Qual sua nacionalidade?",
      [["", "Prefiro não responder"], ["brasileira", "Brasileira"], ["outra", "Outra"]],
      estado.nacionalidade,
      (v) => {
        estado.nacionalidade = v;
        aoMudarFiltro();
      },
    ),
    criarPergunta(
      "pergunta-residencia",
      "Em que país você mora?",
      [["", "Prefiro não responder"], ["BR", "Brasil"], ...paises.map((c) => [c, nomeDoPais(c)]), ["OUTRO", "Outro país"]],
      estado.residencia,
      (v) => {
        estado.residencia = v;
        aoMudarFiltro();
      },
    ),
    criarPergunta(
      "pergunta-organizacao",
      "Você tem ou representa uma organização (CNPJ ou coletivo formalizado)?",
      [["", "Prefiro não responder"], ["sim", "Sim"], ["nao", "Não"]],
      estado.organizacao,
      (v) => {
        estado.organizacao = v;
        aoMudarFiltro();
      },
    ),
  );
}

function renderFiltros() {
  renderAmbitoEPerguntas();
  $("#chips-perfil").replaceChildren(
    ...PERFIS.map((p) =>
      criarChip(p.rotulo, estado.perfil === p.id, () => {
        estado.perfil = estado.perfil === p.id ? "" : p.id;
        aoMudarFiltro();
      }),
    ),
  );
  $("#chips-linguagem").replaceChildren(
    ...LINGUAGENS.map((l) =>
      criarChip(l, estado.linguagens.includes(l), () => {
        estado.linguagens = estado.linguagens.includes(l)
          ? estado.linguagens.filter((x) => x !== l)
          : [...estado.linguagens, l];
        aoMudarFiltro();
      }),
    ),
  );
  const selectUf = el(
    "select",
    {
      class: "chip",
      id: "filtro-uf",
      "aria-label": "Estado (mostra também os editais nacionais)",
      onchange: (e) => {
        estado.uf = e.target.value;
        if (estado.uf) estado.abrangencia = "";
        aoMudarFiltro();
      },
    },
    el("option", { value: "" }, "Todos os estados"),
    UFS.map((uf) => el("option", { value: uf, selected: estado.uf === uf }, uf)),
  );
  $("#chips-abrangencia").replaceChildren(
    criarChip("Nacional", estado.abrangencia === "nacional", () => {
      estado.abrangencia = estado.abrangencia === "nacional" ? "" : "nacional";
      if (estado.abrangencia) estado.uf = "";
      aoMudarFiltro();
    }),
    selectUf,
  );
  $("#chips-apoio").replaceChildren(
    ...TIPOS_APOIO.map((t) =>
      criarChip(t.rotulo, estado.tipoApoio === t.id, () => {
        estado.tipoApoio = estado.tipoApoio === t.id ? "" : t.id;
        aoMudarFiltro();
      }),
    ),
  );
  $("#filtro-abertas").checked = estado.apenasAbertas;
  $("#campo-busca").value = estado.q;
  const ordem = $("#filtro-ordem");
  if (!ordem.options.length) {
    ordem.replaceChildren(...ORDENS.map((o) => el("option", { value: o.id }, o.rotulo)));
  }
  ordem.value = estado.ordem;
  for (const aba of document.querySelectorAll(".aba")) {
    aba.setAttribute("aria-selected", String(aba.dataset.aba === estado.aba));
  }
  const abaLista = document.querySelector('.aba[data-aba="todos"]');
  if (abaLista) abaLista.textContent = estado.ambito === "internacional" ? "Oportunidades" : "Editais";
}

/** O menu do topo acompanha o filtro: "Internacional" fica marcado quando só há oportunidades de fora. */
function atualizarMenu() {
  const fora = estado.ambito === "internacional";
  $('nav [data-nav="editais"]')?.[fora ? "removeAttribute" : "setAttribute"]("aria-current", "page");
  $('nav [data-nav="internacional"]')?.[fora ? "setAttribute" : "removeAttribute"]("aria-current", "page");
}

function aoMudarFiltro() {
  atualizarMenu();
  renderFiltros();
  if (dados) renderContadores();
  renderLista();
  gravarEstado(estado, editalAbertoChave);
}

// ---------------------------------------------------------------- folha de filtros (celular)
// Em telas estreitas a barra "Posso me inscrever?" vira uma folha inferior, aberta por um
// botão fixo "Filtros (n)". No desktop continua a barra de sempre.

const telaEstreita = window.matchMedia("(max-width: 48rem)");
const folha = {
  aberta: false,
  origem: null,
};

function atualizarBotaoFiltros() {
  // A busca fica sempre visível e por isso não entra na conta.
  const n = filtrosAtivos({ ...estado, q: "" }).length;
  $("#filtros-n").textContent = String(n);
  $("#abrir-filtros").setAttribute("aria-label", `Filtros, ${n} ${n === 1 ? "ativo" : "ativos"}. Abrir filtros.`);
  const visiveis = dados ? filtrar(dados.editais, estado, new Date(), chavesServidor).length : 0;
  $("#folha-ver").textContent = visiveis === 1 ? "Ver 1 edital" : `Ver ${visiveis} editais`;
}

function configurarFolha() {
  const secao = $("#filtros");
  secao.setAttribute("role", "dialog");
  secao.setAttribute("aria-modal", "true");
  secao.toggleAttribute("data-aberta", false);
  if (!telaEstreita.matches) {
    secao.removeAttribute("role");
    secao.removeAttribute("aria-modal");
  }
}

function abrirFolha() {
  if (!telaEstreita.matches || folha.aberta) return;
  folha.aberta = true;
  folha.origem = $("#abrir-filtros");
  $("#filtros").setAttribute("data-aberta", "");
  $("#folha-fundo").hidden = false;
  $("#abrir-filtros").setAttribute("aria-expanded", "true");
  document.body.classList.add("folha-aberta");
  $("#folha-fechar").focus();
  document.addEventListener("keydown", aoTeclarNaFolha);
}

function fecharFolha() {
  if (!folha.aberta) return;
  folha.aberta = false;
  $("#filtros").removeAttribute("data-aberta");
  $("#folha-fundo").hidden = true;
  $("#abrir-filtros").setAttribute("aria-expanded", "false");
  document.body.classList.remove("folha-aberta");
  document.removeEventListener("keydown", aoTeclarNaFolha);
  folha.origem?.focus();
}

function aoTeclarNaFolha(evento) {
  if (evento.key === "Escape") {
    evento.preventDefault();
    fecharFolha();
    return;
  }
  if (evento.key !== "Tab") return;
  const focaveis = [...$("#filtros").querySelectorAll("button, input, select, a[href]")].filter((n) => n.offsetParent !== null);
  const primeiro = focaveis[0];
  const ultimo = focaveis.at(-1);
  if (evento.shiftKey && document.activeElement === primeiro) {
    evento.preventDefault();
    ultimo.focus();
  } else if (!evento.shiftKey && document.activeElement === ultimo) {
    evento.preventDefault();
    primeiro.focus();
  }
}

// ---------------------------------------------------------------- cabeçalho, lista, radar

function renderContadores() {
  const c = contadores(dados.editais);
  const titulo = $("#titulo-explorador");
  if (estado.ambito === "internacional") {
    titulo.textContent = "Oportunidades internacionais";
    $("#contadores").replaceChildren(
      el("li", {}, el("span", { class: "valor" }, String(c.internacionaisAbertos)), el("span", { class: "rotulo" }, "oportunidades internacionais abertas")),
      el("li", {}, el("span", { class: "valor" }, String(c.internacionaisEncerrandoEm7Dias)), el("span", { class: "rotulo" }, "encerrando nos próximos 7 dias")),
    );
    return;
  }
  titulo.textContent = estado.ambito === "todos" ? "Editais e oportunidades abertas" : "Editais abertos";
  $("#contadores").replaceChildren(
    el("li", {}, el("span", { class: "valor" }, String(c.abertos)), el("span", { class: "rotulo" }, "editais abertos hoje")),
    el("li", {}, el("span", { class: "valor" }, String(c.encerrandoEm7Dias)), el("span", { class: "rotulo" }, "encerrando nos próximos 7 dias")),
    el(
      "li",
      {},
      el(
        "span",
        { class: "valor", title: c.comValorInformado ? formatarMoeda(c.somaValores) : null },
        c.comValorInformado ? formatarMoedaCompacta(c.somaValores) : "—",
      ),
      el("span", { class: "rotulo" }, `em ${plural(c.comValorInformado, "edital", "editais")} com valor informado (os demais não informam o total)`),
    ),
    estado.ambito === "todos" || c.internacionaisAbertos
      ? el(
          "li",
          {},
          el("span", { class: "valor" }, String(c.internacionaisAbertos)),
          el("span", { class: "rotulo" }, "oportunidades internacionais abertas (filtro Internacional)"),
        )
      : null,
  );
}

function cartaoPadrao(edital) {
  return criarCard(edital, {
    respostas: estado,
    favorito: favoritos.tem(edital.chave),
    aoAbrir: abrirPainel,
    aoAlternarFavorito: alternarFavorito,
  });
}

function renderEstadoVazio(lista) {
  const ativos = filtrosAtivos(estado);
  const dica = ativos.length
    ? `Filtros ativos: ${ativos.map((a) => a.rotulo).join(" · ")}. Tente remover algum deles ou use outro perfil.`
    : "Ainda não há editais abertos nesta base.";
  lista.replaceChildren(
    el(
      "li",
      { style: "grid-column:1/-1" },
      el(
        "div",
        { class: "estado" },
        el("h3", {}, "Nenhum edital com esses filtros"),
        el("p", {}, dica),
        ativos.length || !estado.apenasAbertas
          ? el("button", { type: "button", class: "btn btn-primario", onclick: limparFiltros }, "Limpar filtros")
          : null,
      ),
    ),
  );
}

function limparFiltros() {
  const { aba, ambito } = estado;
  estado = { ...lerEstado("").estado, aba, ambito };
  aoMudarFiltro();
}

function renderLista() {
  if (!dados) return;
  const lista = $("#lista");
  const info = $("#resultado-info");
  atualizarContadorFavoritos();
  $("#barra-exportar").hidden = estado.aba !== "favoritos";
  $("#filtros").hidden = estado.aba === "favoritos";
  $("#abrir-filtros").hidden = estado.aba === "favoritos";
  atualizarBotaoFiltros();
  if (estado.aba === "favoritos") return renderFavoritos(lista, info);
  const visiveis = ordenar(filtrar(dados.editais, estado, new Date(), chavesServidor), estado.ordem);
  const universo = dados.editais.filter((e) =>
    estado.ambito === "todos" ? true : estado.ambito === "internacional" ? ehInternacional(e) : !ehInternacional(e),
  ).length;
  const nome = estado.ambito === "internacional" ? ["oportunidade", "oportunidades"] : ["edital", "editais"];
  info.textContent = `${plural(visiveis.length, ...nome)} de ${universo} abertos`;
  if (!visiveis.length) return renderEstadoVazio(lista);
  lista.replaceChildren(...visiveis.map(cartaoPadrao));
}

function renderFavoritos(lista, info) {
  const itens = resolverFavoritos(favoritos, dados.editais);
  info.textContent = itens.length
    ? `${plural(itens.length, "favorito", "favoritos")}${favoritos.persistente ? "" : " (só nesta sessão)"}`
    : "";
  if (!itens.length) {
    lista.replaceChildren(
      el(
        "li",
        { style: "grid-column:1/-1" },
        el("div", { class: "estado" }, el("h3", {}, "Nenhum favorito ainda"), el("p", {}, "Use o marcador no canto de cada edital para guardá-lo aqui, exportar para o calendário ou compartilhar.")),
      ),
    );
    return;
  }
  lista.replaceChildren(
    ...itens.map(({ salvo, edital }) =>
      edital ? cartaoPadrao(edital) : criarCardRemovido(salvo, { aoRemover: (chave) => { favoritos.remover(chave); renderLista(); anunciar("Removido dos favoritos."); } }),
    ),
  );
}

function renderRodape() {
  const texto = $("#fonte-texto");
  const ponto = $("#fonte-ponto");
  if (dados.origem === "ao-vivo") {
    texto.textContent = "Dados ao vivo";
    ponto.classList.remove("offline");
  } else {
    const data = formatarDataHora(dados.geradoEm);
    texto.textContent = dados.atualizando ? `Dados de ${data}. Atualizando…` : `Dados de ${data} (cópia offline)`;
    ponto.classList.add("offline");
    if (dados.motivoFallback) texto.title = `Supabase indisponível: ${dados.motivoFallback}`;
  }
}

function atualizarContadorFavoritos() {
  $("#contador-favoritos").textContent = String(favoritos.total);
}

// ---------------------------------------------------------------- painel e favoritos

function sincronizarBotoesFavorito(chave) {
  const marcado = favoritos.tem(chave);
  for (const botao of document.querySelectorAll(`[data-favorito="${CSS.escape(chave)}"]`)) {
    botao.setAttribute("aria-pressed", String(marcado));
  }
}

function alternarFavorito(edital) {
  const marcado = favoritos.alternar(edital);
  sincronizarBotoesFavorito(edital.chave);
  atualizarContadorFavoritos();
  const aviso = marcado ? "Adicionado aos favoritos." : "Removido dos favoritos.";
  anunciar(favoritos.persistente ? aviso : `${aviso} Não foi possível salvar no navegador: vale só até fechar a página.`);
  if (estado.aba === "favoritos" && !painel.aberto) renderLista();
}

function opcoesDoPainel(edital) {
  return {
    respostas: estado,
    favorito: favoritos.tem(edital.chave),
    aoAlternarFavorito: alternarFavorito,
    urlPortal: urlDoPortal(edital.chave),
    aoFechar: fecharPainel,
    aoCopiar: async (texto) => {
      const ok = await copiarTexto(texto);
      anunciar(ok ? "Resumo copiado. É só colar no WhatsApp." : "Não foi possível copiar. Selecione e copie o texto manualmente.");
    },
  };
}

function abrirPainel(edital, origem) {
  editalAbertoChave = edital.chave;
  empilharPainel(estado, edital.chave);
  painel.abrir(edital, opcoesDoPainel(edital), origem);
}

function fecharPainel() {
  if (history.state?.painel) history.back(); // o popstate fecha de fato
  else ocultarPainel();
}

function ocultarPainel() {
  editalAbertoChave = null;
  painel.fechar();
  gravarEstado(estado, null);
}

/** Aplica ?edital= da URL (carga inicial, voltar/avançar). */
function sincronizarPainelComUrl() {
  const { edital } = lerEstado();
  if (!edital) {
    if (painel.aberto) {
      editalAbertoChave = null;
      painel.fechar();
    }
    return;
  }
  const encontrado = dados.editais.find((e) => e.chave === edital);
  if (!encontrado) {
    anunciar("Este edital não está mais na base (encerrado ou removido).");
    editalAbertoChave = null;
    gravarEstado(estado, null);
    return;
  }
  editalAbertoChave = edital;
  painel.abrir(encontrado, opcoesDoPainel(encontrado), null);
}

// ---------------------------------------------------------------- eventos

function ligarEventos() {
  $("#campo-busca").addEventListener("input", (e) => {
    clearTimeout(temporizadorBusca);
    temporizadorBusca = setTimeout(async () => {
      estado.q = e.target.value.trim();
      chavesServidor = null;
      if (estado.q && dados.buscarNoServidor) {
        try {
          chavesServidor = await dados.buscarNoServidor(estado.q);
        } catch (erro) {
          console.warn("Busca no servidor falhou; usando só a busca local:", erro.message);
        }
      }
      renderLista();
      gravarEstado(estado, editalAbertoChave);
    }, 250);
  });
  $("#filtro-abertas").addEventListener("change", (e) => {
    estado.apenasAbertas = e.target.checked;
    aoMudarFiltro();
  });
  $("#filtro-ordem").addEventListener("change", (e) => {
    estado.ordem = e.target.value;
    aoMudarFiltro();
  });
  for (const aba of document.querySelectorAll(".aba")) {
    aba.addEventListener("click", () => {
      estado.aba = aba.dataset.aba;
      aoMudarFiltro();
    });
  }
  $("#abrir-filtros").addEventListener("click", abrirFolha);
  $("#folha-fechar").addEventListener("click", fecharFolha);
  $("#folha-ver").addEventListener("click", fecharFolha);
  $("#folha-fundo").addEventListener("click", fecharFolha);
  telaEstreita.addEventListener("change", () => {
    fecharFolha();
    configurarFolha();
  });
  $("#exportar-csv").addEventListener("click", () => exportar("csv"));
  $("#exportar-ics").addEventListener("click", () => exportar("ics"));
  $("#exportar-whatsapp").addEventListener("click", () => exportar("whatsapp"));
  window.addEventListener("popstate", () => {
    // Só redesenha a lista se os filtros mudaram: abrir/fechar o painel não deve
    // recriar os cards (o foco precisa voltar ao botão que abriu o painel).
    const novo = lerEstado().estado;
    if (JSON.stringify(novo) !== JSON.stringify(estado)) {
      estado = novo;
      renderFiltros();
      renderLista();
    }
    sincronizarPainelComUrl();
  });
  // Contagens regressivas e "Encerrado" acompanham o relógio.
  setInterval(() => {
    atualizarBadges(document);
    renderContadores();
  }, 60_000);
}

async function exportar(formato) {
  const itens = resolverFavoritos(favoritos, dados.editais);
  if (!itens.length) return anunciar("Você ainda não tem favoritos para exportar.");
  if (formato === "csv") {
    baixarArquivo("editais-favoritos.csv", gerarCsv(itens, urlDoPortal), "text/csv;charset=utf-8");
    anunciar("CSV gerado.");
  } else if (formato === "ics") {
    baixarArquivo("editais-favoritos.ics", gerarIcs(itens, urlDoPortal), "text/calendar;charset=utf-8");
    anunciar("Arquivo de calendário gerado.");
  } else {
    const ok = await copiarTexto(gerarTextoWhatsapp(itens, urlDoPortal));
    anunciar(ok ? "Lista copiada. É só colar no WhatsApp." : "Não foi possível copiar a lista.");
  }
}

// ---------------------------------------------------------------- início

function mostrarEsqueleto() {
  $("#lista").replaceChildren(...Array.from({ length: 6 }, () => el("li", {}, el("div", { class: "esqueleto" }))));
  $("#lista").setAttribute("aria-busy", "true");
}

function mostrarErro(erro) {
  console.error(erro);
  $("#lista").setAttribute("aria-busy", "false");
  $("#lista").replaceChildren(
    el(
      "li",
      { style: "grid-column:1/-1" },
      el(
        "div",
        { class: "estado" },
        el("h3", {}, "Não foi possível carregar os editais"),
        el("p", {}, "O banco e a cópia offline falharam. Verifique sua conexão e tente de novo."),
        el("button", { type: "button", class: "btn btn-primario", onclick: () => location.reload() }, "Tentar novamente"),
      ),
    ),
  );
  $("#fonte-texto").textContent = "Sem dados";
}

/** Chamado com o snapshot e, depois, com os dados ao vivo: redesenha sem perder filtros nem painel. */
async function aplicarDados(novos) {
  const primeira = dados === null;
  dados = novos;
  $("#lista").setAttribute("aria-busy", "false");
  const aviso = $("#aviso-topo");
  aviso.hidden = !dados.motivoFallback;
  if (dados.motivoFallback) aviso.textContent = "O banco ao vivo não respondeu; mostrando a cópia offline mais recente.";
  chavesServidor = null;
  if (estado.q && dados.buscarNoServidor) {
    chavesServidor = await dados.buscarNoServidor(estado.q).catch(() => null);
  }
  renderContadores();
  renderAmbitoEPerguntas(); // a lista de países da pergunta "Em que país você mora?" vem dos dados
  renderRodape();
  renderRadar($("#radar"), $("#radar-lista"), dados.radar);
  preencherUltimaColeta(dados.geradoEm);
  renderLista();
  if (primeira) sincronizarPainelComUrl();
  document.documentElement.dataset.pronto = "1"; // sinal para testes automatizados
  document.documentElement.dataset.origem = dados.origem;
}

async function iniciar() {
  iniciarPagina("editais");
  atualizarMenu();
  renderFiltros();
  configurarFolha();
  ligarEventos();
  mostrarEsqueleto();
  atualizarContadorFavoritos();
  try {
    await carregarProgressivo(config, aplicarDados);
  } catch (erro) {
    mostrarErro(erro);
  }
}

iniciar();
