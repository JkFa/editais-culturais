// Contador de visitas. LGPD: nada de IP, cookie ou identificador persistente.
// O cliente só avisa "uma visita, nesta página"; quem agrega por dia × UF × cidade é a
// Edge Function. Uma visita por aba e por dia (sessionStorage), sem bots (webdriver).
// Do Not Track e Global Privacy Control são respeitados no servidor (conta sem localização).

import { configurado } from "./dados.js";

const PAGINAS = new Set(["home", "editais", "sobre"]);

function hojeLocal() {
  return new Date().toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" });
}

/** Dispara e esquece: nunca atrasa nem quebra a página. */
export function registrarVisita(config, pagina) {
  try {
    if (!configurado(config) || !PAGINAS.has(pagina) || navigator.webdriver) return;
    const chave = `editais-culturais:visita:${hojeLocal()}`;
    if (sessionStorage.getItem(chave)) return;
    sessionStorage.setItem(chave, "1");
    // text/plain evita o preflight de CORS, que não combina com keepalive.
    fetch(`${config.supabaseUrl.replace(/\/$/, "")}/functions/v1/registrar-visita`, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=UTF-8" },
      body: JSON.stringify({ pagina }),
      keepalive: true,
      credentials: "omit",
      referrerPolicy: "no-referrer",
    }).catch(() => {});
  } catch {
    // sessionStorage bloqueado ou fetch indisponível: sem contagem, sem problema
  }
}
