// Tema: claro (padrão) e escuro (opcional). O site sempre abre no claro, sem consultar a
// preferência do sistema. A escolha é preferência de interface, guardada em localStorage
// (não é rastreio). Se o armazenamento falhar, vale só nesta página.
// O script inline no <head> de cada página aplica o escuro antes do primeiro paint.

const CHAVE = "editais-culturais:tema";
const CORES = { claro: "#f7f5f0", escuro: "#161513" };
const PROXIMO = { claro: "escuro", escuro: "claro" };

export function temaSalvo() {
  try {
    return localStorage.getItem(CHAVE) === "escuro" ? "escuro" : "claro";
  } catch {
    return "claro";
  }
}

export function aplicarTema(modo) {
  const raiz = document.documentElement;
  if (modo === "escuro") raiz.dataset.tema = "escuro";
  else delete raiz.dataset.tema;
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
    meta.setAttribute("content", CORES[modo]);
  }
}

function guardar(modo) {
  try {
    if (modo === "escuro") localStorage.setItem(CHAVE, "escuro");
    else localStorage.removeItem(CHAVE);
  } catch {
    // sem armazenamento: a escolha vale só nesta página
  }
}

function atualizarBotao(botao, modo) {
  botao.textContent = `Tema: ${modo}`;
  botao.setAttribute("aria-label", `Tema: ${modo}. Mudar para ${PROXIMO[modo]}.`);
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
