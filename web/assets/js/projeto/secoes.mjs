// Lógica pura das specs de seção, compartilhada pelo navegador e pela Edge Function.
// Fonte única em specs/lib/; `node scripts/sincronizar_specs.mjs` copia para os dois destinos.

const LITERAL = /^(true|false|null|-?\d+(?:\.\d+)?|"[^"]*")$/;

const PROIBIDAS = new Set(["__proto__", "constructor", "prototype"]);

function lerCaminho(ctx, caminho) {
  return caminho
    .split(".")
    .reduce((o, k) => (o != null && typeof o === "object" && !PROIBIDAS.has(k) && Object.hasOwn(o, k) ? o[k] : undefined), ctx);
}

function lerLiteral(txt) {
  const t = txt.trim();
  if (!LITERAL.test(t)) throw new Error(`literal inválido: ${txt}`);
  return JSON.parse(t);
}

function avaliarTermo(termo, ctx) {
  const m = termo.match(/^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*(?:(==|!=)\s*(.+?))?\s*$/);
  if (!m) throw new Error(`condição inválida: ${termo}`);
  const valor = lerCaminho(ctx, m[1]);
  if (!m[2]) return Boolean(valor);
  const esperado = lerLiteral(m[3]);
  const igual = (valor ?? null) === esperado;
  return m[2] === "==" ? igual : !igual;
}

/** Expressões como `ficha.x == true`, `a != "b" && c`. Sem eval; caminho ausente vale null. */
export function avaliarCondicao(expr, ctx) {
  return expr.split("||").some((ou) => ou.split("&&").every((e) => avaliarTermo(e, ctx)));
}

/** Perguntas que valem agora: as fixas + as condicionais cuja condição é verdadeira. */
export function perguntasAplicaveis(spec, ctx, { canal } = {}) {
  const todas = [...(spec.perguntas ?? [])];
  for (const c of spec.condicionais ?? []) {
    if (avaliarCondicao(c.se, ctx)) todas.push(c.pergunta);
  }
  return canal ? todas.filter((p) => (p.canal ?? "chat") === canal) : todas;
}

/** Obrigatórias do chat que ainda não têm resposta. `respondidas` é um Set de pergunta_id. */
export function pendentes(spec, ctx, respondidas) {
  return perguntasAplicaveis(spec, ctx, { canal: "chat" }).filter(
    (p) => p.obrigatoria !== false && !respondidas.has(p.id),
  );
}

export function secoesDoFluxo(secoes, fluxo) {
  return secoes
    .filter((s) => s.ativo === false || (s.fluxos ?? []).includes(fluxo))
    .sort((a, b) => a.ordem - b.ordem);
}
