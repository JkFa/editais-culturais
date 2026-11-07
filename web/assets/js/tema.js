// Tema: sistema (padrão) -> claro -> escuro. A escolha é preferência de interface,
// guardada em localStorage (não é rastreio). Se o armazenamento falhar, segue o sistema.
// O script inline no <head> de cada página aplica o tema antes do primeiro paint.

const CHAVE = "editais-culturais:tema";
const CORES = { claro: "#f7f5f0", escuro: "#161513" };
const PROXIMO = { sistema: "claro", claro: "escuro", escuro: "sistema" };
const ROTULO = { sistema: "sistema", claro: "claro", escuro: "escuro" };

export function temaSalvo() {
  try {
    const valor = localStorage.getItem(CHAVE);
    return valor === "claro" || valor === "escuro" ? valor : "sistema";
  } catch {
    return "sistema";
  }
}

export function aplicarTema(modo) {
  const raiz = document.documentElement;
  const metas = document.querySelectorAll('meta[name="theme-color"]');
  if (modo === "sistema") {
    delete raiz.dataset.tema;
    for (const meta of metas) meta.setAttribute("content", meta.dataset.padrao ?? meta.content);
  } else {
    raiz.dataset.tema = modo;
    for (const meta of metas) {
      meta.dataset.padrao ??= meta.content;
      meta.setAttribute("content", CORES[modo]);
    }
  }
}

function guardar(modo) {
  try {
    if (modo === "sistema") localStorage.removeItem(CHAVE);
    else localStorage.setItem(CHAVE, modo);
  } catch {
    // sem armazenamento: a escolha vale só nesta página
  }
}

function atualizarBotao(botao, modo) {
  botao.textContent = `Tema: ${ROTULO[modo]}`;
  botao.setAttribute("aria-label", `Tema: ${ROTULO[modo]}. Mudar para ${ROTULO[PROXIMO[modo]]}.`);
  botao.dataset.modo = modo;
}

export function iniciarTema() {
  let modo = temaSalvo();
  aplicarTema(modo);
  const botoes = document.querySelectorAll("[data-alternar-tema]");
  for (const botao of botoes) {
    atualizarBotao(botao, modo);
    botao.addEventListener("click", () => {
      modo = PROXIMO[modo];
      guardar(modo);
      aplicarTema(modo);
      for (const outro of botoes) atualizarBotao(outro, modo);
    });
  }
}
