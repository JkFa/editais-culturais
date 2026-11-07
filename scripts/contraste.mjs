// Valida o contraste (WCAG 2.x) de todos os pares de cor usados, nos dois temas.
// Lê os tokens de web/assets/css/tema.css. Uso: node scripts/contraste.mjs
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const css = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "web", "assets", "css", "tema.css"),
  "utf8",
);

function tokensDoBloco(inicio) {
  const i = css.indexOf(inicio);
  if (i < 0) throw new Error(`bloco não encontrado: ${inicio}`);
  const abre = css.indexOf("{", i);
  let nivel = 0;
  let fim = abre;
  for (; fim < css.length; fim++) {
    if (css[fim] === "{") nivel++;
    if (css[fim] === "}" && --nivel === 0) break;
  }
  const corpo = css.slice(abre + 1, fim);
  return Object.fromEntries(
    [...corpo.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]),
  );
}

const claro = tokensDoBloco(":root {");
const escuroForcado = tokensDoBloco(':root[data-tema="escuro"]');
const escuroSistema = tokensDoBloco(':root:not([data-tema="claro"])');
const escuro = { ...claro, ...escuroForcado };

function analisar(valor) {
  valor = valor.trim();
  if (valor === "transparent") return [0, 0, 0, 0];
  let m = valor.match(/^#([0-9a-f]{6})$/i);
  if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16)).concat(1);
  m = valor.match(/^rgba?\(([^)]+)\)$/);
  if (m) {
    const [r, g, b, a = 1] = m[1].split(",").map(Number);
    return [r, g, b, a];
  }
  throw new Error(`cor não suportada: ${valor}`);
}

// Compõe uma cor com alfa sobre uma base opaca.
const compor = ([r, g, b, a], [r2, g2, b2]) => [
  r * a + r2 * (1 - a),
  g * a + g2 * (1 - a),
  b * a + b2 * (1 - a),
];
const luz = ([r, g, b]) => {
  const f = (c) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};
const razao = (a, b) => {
  const [x, y] = [luz(a), luz(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

let falhas = 0;
// `sobre`: superfície opaca sob um fundo translúcido (badges no tema escuro).
function conferir(tema, tokens, rotulo, fgToken, bgToken, minimo, sobre) {
  const base = analisar(tokens[sobre ?? bgToken]).slice(0, 3);
  const bg = compor(analisar(tokens[bgToken]), base);
  const fg = compor(analisar(tokens[fgToken]), bg);
  const r = razao(fg, bg);
  const ok = r >= minimo;
  if (!ok) falhas++;
  const marca = ok ? "ok   " : "FALHA";
  console.log(`${marca} ${tema.padEnd(6)} ${r.toFixed(2).padStart(5)} (mín ${minimo})  ${rotulo}`);
}

const superficies = ["--cor-fundo", "--cor-superficie", "--cor-superficie-elevada"];
for (const [tema, t] of [
  ["claro", claro],
  ["escuro", escuro],
]) {
  for (const s of superficies) {
    conferir(tema, t, `texto em ${s}`, "--cor-texto", s, 4.5);
    conferir(tema, t, `texto suave em ${s}`, "--cor-texto-suave", s, 4.5);
    conferir(tema, t, `urgente (texto) em ${s}`, "--cor-urgente", s, 4.5);
    conferir(tema, t, `aberto (texto) em ${s}`, "--cor-aberto", s, 4.5);
    conferir(tema, t, `borda de controle em ${s}`, "--cor-borda-controle", s, 3);
    conferir(tema, t, `foco em ${s}`, "--cor-foco", s, 3);
  }
  conferir(tema, t, "urgente sobre fundo suave (badge)", "--cor-urgente", "--cor-urgente-suave", 4.5, "--cor-superficie");
  conferir(tema, t, "aberto sobre fundo suave (badge)", "--cor-aberto", "--cor-aberto-suave", 4.5, "--cor-superficie");
  conferir(tema, t, "badge 'encerra hoje'", "--cor-urgente-sobre", "--cor-urgente-fundo", 4.5, "--cor-superficie");
  conferir(tema, t, "texto em destaque", "--cor-texto", "--cor-destaque-fundo", 4.5);
  conferir(tema, t, "texto suave em destaque", "--cor-texto-suave", "--cor-destaque-fundo", 4.5);
  conferir(tema, t, "chip marcado (fundo sobre texto)", "--cor-fundo", "--cor-texto", 4.5);
  for (let n = 0; n <= 5; n++) {
    conferir(tema, t, `cartograma nível ${n}`, `--cg-t-${n}`, `--cg-${n}`, 4.5);
  }
  conferir(tema, t, "cartograma zero × fundo", "--cg-0", "--cor-fundo", 1.05);
  conferir(tema, t, "borda tracejada do zero × zero", "--cor-borda-controle", "--cg-0", 3);
  for (let n = 1; n <= 5; n++) {
    conferir(tema, t, `cartograma nível ${n} × fundo`, `--cg-${n}`, "--cor-fundo", 1.05);
  }
}

const ordenar = (o) => JSON.stringify(Object.entries(o).sort());
if (ordenar(escuroForcado) === ordenar(escuroSistema)) {
  console.log("ok    blocos escuros (sistema e forçado) idênticos");
} else {
  falhas++;
  console.log("FALHA o bloco escuro de sistema difere do escuro forçado");
}

console.log(falhas ? `\n${falhas} falha(s)` : "\nContraste OK nos dois temas");
process.exit(falhas ? 1 : 0);
