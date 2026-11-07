// Orquestra a página: dados, filtros, URL, lista, painel, favoritos e radar.
import { atualizarBadges, criarCard, criarCardRemovido } from "./cards.js";
import { carregarDados } from "./dados.js";
import { Painel, copiarTexto } from "./drawer.js";
import {
  Favoritos,
  baixarArquivo,
  gerarCsv,
  gerarIcs,
  gerarTextoWhatsapp,
  resolverFavoritos,
} from "./favoritos.js";
import { LINGUAGENS, ORDENS, PERFIS, UFS, contadores, filtrar, filtrosAtivos, ordenar } from "./filtros.js";
import { el, formatarDataHora, formatarMoeda, plural } from "./formatos.js";
import { mesAtualBrasilia, mesesAteJanela } from "./prazos.js";
import { empilharPainel, gravarEstado, lerEstado, urlDoPortal } from "./url-state.js";

const $ = (seletor) => document.querySelector(seletor);
const config = window.EDITAIS_CONFIG ?? {};

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

function renderFiltros() {
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
}

function aoMudarFiltro() {
  renderFiltros();
  renderLista();
  gravarEstado(estado, editalAbertoChave);
}

// ---------------------------------------------------------------- cabeçalho, lista, radar

function renderContadores() {
  const c = contadores(dados.editais);
  $("#contadores").replaceChildren(
    el("li", {}, el("span", { class: "valor" }, String(c.abertos)), el("span", { class: "rotulo" }, "editais abertos hoje")),
    el("li", {}, el("span", { class: "valor" }, String(c.encerrandoEm7Dias)), el("span", { class: "rotulo" }, "encerrando nos próximos 7 dias")),
    el(
      "li",
      {},
      el("span", { class: "valor" }, c.comValorInformado ? formatarMoeda(c.somaValores) : "—"),
      el("span", { class: "rotulo" }, `em ${plural(c.comValorInformado, "edital", "editais")} com valor informado (os demais não informam o total)`),
    ),
  );
}

function cartaoPadrao(edital) {
  return criarCard(edital, {
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
  const aba = estado.aba;
  estado = { ...lerEstado("").estado, aba };
  aoMudarFiltro();
}

function renderLista() {
  if (!dados) return;
  const lista = $("#lista");
  const info = $("#resultado-info");
  atualizarContadorFavoritos();
  $("#barra-exportar").hidden = estado.aba !== "favoritos";
  $("#filtros").hidden = estado.aba === "favoritos";
  if (estado.aba === "favoritos") return renderFavoritos(lista, info);
  const visiveis = ordenar(filtrar(dados.editais, estado, new Date(), chavesServidor), estado.ordem);
  info.textContent = `${plural(visiveis.length, "edital", "editais")} de ${dados.editais.length} abertos`;
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

function renderRadar() {
  const secao = $("#radar");
  const itens = dados.radar ?? [];
  if (!itens.length) {
    secao.hidden = true;
    return;
  }
  const mes = mesAtualBrasilia();
  const ordenados = [...itens].sort((a, b) => {
    const da = mesesAteJanela(a.janela_mes_inicio, a.janela_mes_fim, mes) ?? 99;
    const db = mesesAteJanela(b.janela_mes_inicio, b.janela_mes_fim, mes) ?? 99;
    return da - db;
  });
  secao.hidden = false;
  $("#radar-lista").replaceChildren(
    ...ordenados.map((r) =>
      el(
        "li",
        {},
        el(
          "div",
          {},
          el("div", { class: "titulo-radar" }, r.url_referencia ? el("a", { href: r.url_referencia, target: "_blank", rel: "noopener noreferrer" }, r.titulo) : r.titulo),
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

function renderRodape() {
  const texto = $("#fonte-texto");
  const ponto = $("#fonte-ponto");
  if (dados.origem === "ao-vivo") {
    texto.textContent = "Dados ao vivo";
    ponto.classList.remove("offline");
  } else {
    texto.textContent = `Dados de ${formatarDataHora(dados.geradoEm)} (cópia offline)`;
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

async function iniciar() {
  renderFiltros();
  ligarEventos();
  mostrarEsqueleto();
  atualizarContadorFavoritos();
  try {
    dados = await carregarDados(config);
  } catch (erro) {
    return mostrarErro(erro);
  }
  $("#lista").setAttribute("aria-busy", "false");
  if (dados.motivoFallback) {
    $("#aviso-topo").hidden = false;
    $("#aviso-topo").textContent = "O banco ao vivo não respondeu; mostrando a cópia offline mais recente.";
  }
  if (estado.q && dados.buscarNoServidor) {
    chavesServidor = await dados.buscarNoServidor(estado.q).catch(() => null);
  }
  renderContadores();
  renderRodape();
  renderRadar();
  renderLista();
  sincronizarPainelComUrl();
  document.documentElement.dataset.pronto = "1"; // sinal para testes automatizados
}

iniciar();
