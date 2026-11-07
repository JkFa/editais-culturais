// Funções puras do texto exportado (testáveis em Node, sem DOM nem a biblioteca docx).
const MARCADOR = /(\[COMPLETAR:[^\]]*\])/g;

/** Parte um parágrafo em pedaços; os [COMPLETAR: ...] ficam marcados para o destaque amarelo. */
export function partirTexto(texto) {
  return texto.split(MARCADOR).filter(Boolean).map((t) => ({ texto: t, completar: t.startsWith("[COMPLETAR:") }));
}

export function paragrafos(texto) {
  return texto.split(/\n+/).map((p) => p.trim()).filter(Boolean);
}

export function nomeDoArquivo(titulo) {
  const base = titulo.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
  return `projeto-${base || "cultural"}.docx`;
}

/** Texto para colar em outra plataforma: sem os marcadores de pendência não resolvidos? Não: mantém, para a pessoa ver. */
export const textoParaCopiar = (texto) => texto.trim();
