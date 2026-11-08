// Copia a fonte única das specs (specs/) para onde o front e a Edge Function leem.
// Uso: node scripts/sincronizar_specs.mjs          → escreve
//      node scripts/sincronizar_specs.mjs --check  → sai com 1 se algo estiver fora de sincronia
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const raiz = join(dirname(fileURLToPath(import.meta.url)), "..");
const ler = (...p) => readFileSync(join(raiz, ...p), "utf8");
const arquivos = readdirSync(join(raiz, "specs", "secoes")).filter((f) => f.endsWith(".json")).sort();
const secoes = arquivos.map((f) => JSON.parse(ler("specs", "secoes", f))).sort((a, b) => a.ordem - b.ordem);
const checagens = JSON.parse(ler("specs", "checagens.json"));
const versao = createHash("sha256").update(JSON.stringify({ secoes, checagens })).digest("hex").slice(0, 12);
const pacote = JSON.stringify({ versao, secoes, checagens }, null, 1) + "\n";
// Módulos puros compartilhados (um copia por destino; os imports entre eles são relativos, "./x.mjs").
const modulos = readdirSync(join(raiz, "specs", "lib")).filter((f) => f.endsWith(".mjs") && !f.endsWith(".test.mjs")).sort();

const SAIDAS = [
  ["web/projeto/specs.json", pacote],
  ["supabase/functions/assistente/specs.gen.json", pacote],
  ...modulos.flatMap((m) => [
    [`web/assets/js/projeto/${m}`, ler("specs", "lib", m)],
    [`supabase/functions/assistente/${m}`, ler("specs", "lib", m)],
  ]),
];

const check = process.argv.includes("--check");
let fora = 0;
for (const [caminho, conteudo] of SAIDAS) {
  const destino = join(raiz, caminho);
  const atual = existsSync(destino) ? readFileSync(destino, "utf8") : null;
  if (atual === conteudo) continue;
  fora++;
  if (check) console.error(`fora de sincronia: ${caminho}`);
  else {
    mkdirSync(dirname(destino), { recursive: true });
    writeFileSync(destino, conteudo);
    console.log(`escrito: ${caminho}`);
  }
}
if (check && fora) {
  console.error("Rode: node scripts/sincronizar_specs.mjs");
  process.exit(1);
}
console.log(check ? "specs em sincronia" : `specs sincronizadas (versão ${versao})`);
