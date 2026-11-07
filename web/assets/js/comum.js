// Inicialização comum às páginas: tema, contagem de visita e rodapé.
import { iniciarTema } from "./tema.js";
import { registrarVisita } from "./visitas.js";
import { formatarDataHora } from "./formatos.js";

export const config = window.EDITAIS_CONFIG ?? {};

export function iniciarPagina(pagina) {
  iniciarTema();
  registrarVisita(config, pagina);
}

/** "Última coleta: 07/11/2026 00:00" no rodapé, quando houver. */
export function preencherUltimaColeta(geradoEm) {
  for (const alvo of document.querySelectorAll("[data-ultima-coleta]")) {
    alvo.textContent = geradoEm ? formatarDataHora(geradoEm) : "indisponível";
  }
}
