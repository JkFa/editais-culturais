// Checagens cruzadas entre seções (objetivos × metas × cronograma × orçamento × equipe).
// DETERMINÍSTICO: nenhuma chamada de LLM aqui. Compartilhado pelo navegador (painel "Pendências do
// projeto") e pela Edge Function. Fonte única em specs/lib/; `node scripts/sincronizar_specs.mjs` copia.
//
// Cada pendência: { id, regra, nivel, secao, mensagem, itemId? }
//   nivel: "erro" (vai contra o edital) | "aviso" | "info" | "nao_verificado" (a regra não pôde rodar)
import { ETAPAS, ROTULOS_ETAPA, resumoOrcamento, semAcento, totalDoItem, valorAConfirmar } from "./tabelas.mjs";

const brl = (n) => `R$ ${Number(n).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (x) => `${(x * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
const resumido = (t, n = 80) => {
  const x = String(t ?? "").trim() || "(sem nome)";
  return x.length > n ? `${x.slice(0, n - 1).trim()}…` : x;
};

// ── texto → itens ────────────────────────────────────────────────────────────────────────────

const MARCADOR = /^\s*(?:[-•*–]|\d+\s*[.)\-–:]|(?:meta|objetivo(?:\s+espec[ií]fico)?)\s*\d+\s*[.):\-–]?)\s*(.+)$/i;

/** Itens de uma lista escrita (numerada, com marcadores ou uma frase por linha). */
export function extrairItens(texto, { ignorar } = {}) {
  const linhas = String(texto ?? "").split(/\n+/).map((l) => l.trim()).filter(Boolean);
  const itens = [];
  for (const l of linhas) {
    const m = l.match(MARCADOR);
    const t = (m ? m[1] : l).replace(/^\*+|\*+$/g, "").trim();
    if (t.length < 12) continue;
    if (/^(objetivos?\s+espec[ií]ficos?|metas?)\s*:?\s*$/i.test(t)) continue;
    if (ignorar?.test(semAcento(t))) continue;
    itens.push(t);
  }
  // Texto corrido sem marcadores: cada frase vira um item.
  if (itens.length === 1 && itens[0].length > 200) {
    return itens[0].split(/(?<=[.;])\s+/).map((s) => s.trim()).filter((s) => s.length >= 12);
  }
  return itens;
}

const OBJETIVO_GERAL = /^objetivo geral|^o objetivo geral/;

export const objetivosEspecificos = (texto) => extrairItens(texto, { ignorar: OBJETIVO_GERAL });
export const metasDoTexto = (texto) => extrairItens(texto);

// ── ligação por palavras (sem LLM) ───────────────────────────────────────────────────────────

const VAZIAS = new Set(
  ("para com sobre entre ate durante apos antes como uma uns umas seus suas pelo pela pelos pelas nosso nossa " +
    "esse essa esses essas este esta estes estas aquele aquela mais menos muito muita todo toda todos todas " +
    "cada onde quando que dos das nos nas num numa por sem sob ser sao foi ate meta metas objetivo objetivos " +
    "realizar promover garantir oferecer produzir ampliar fortalecer desenvolver criar apresentar executar " +
    "alcancar atingir proporcionar possibilitar mes meses ano anos projeto atividade atividades")
    .split(" "),
);
const radicais = (t) =>
  new Set(
    semAcento(t)
      .replace(/[^a-z0-9\s]/g, " ")
      .split(/\s+/)
      .filter((p) => p.length >= 4 && !VAZIAS.has(p) && !/^\d+$/.test(p))
      .map((p) => p.slice(0, 5)),
  );

/** Há ao menos um radical de conteúdo em comum entre o texto e algum dos candidatos? */
export function temLigacao(texto, candidatos) {
  const a = radicais(texto);
  if (!a.size) return true; // sem palavra de conteúdo não dá para afirmar que falta
  return candidatos.some((c) => {
    const b = radicais(c);
    for (const r of a) if (b.has(r)) return true;
    return false;
  });
}

// ── regras ───────────────────────────────────────────────────────────────────────────────────

const MEDIDAS_ACESSIBILIDADE = [
  { nome: "intérprete de Libras", chaves: ["libras", "interprete"] },
  { nome: "audiodescrição", chaves: ["audiodescri"] },
  { nome: "legendas", chaves: ["legenda"] },
  { nome: "material em braile", chaves: ["braile"] },
  { nome: "material em fonte ampliada", chaves: ["fonte ampliada", "letra ampliada", "ampliad"] },
];
const PESSOAL = /(equipe|cach[eê]|pessoal|honor[aá]rio|remunera|recursos humanos|m[aã]o de obra)/i;
const CANAL_PAGO = /(patroc|impuls|an[uú]ncio|anuncio|pago|paga|gr[aá]fica|impress|outdoor|r[aá]dio|tv)/i;

/**
 * @param {object} e
 * @param {Array<{id:string,titulo:string,limite_caracteres_padrao?:number}>} e.specs
 * @param {Record<string,{rascunho:string}>} e.secoes   texto por id de seção
 * @param {Array} e.orcamento  @param {Array} e.cronograma  @param {Array} e.midia  @param {Array} e.equipe
 * @param {object|null} e.criterios  linha de edital_criterios (ou null)
 * @param {object} e.ficha
 * @param {{objetivos?:Array<{texto:string,tem_meta:boolean}>}|null} e.ligacoes  classificação opcional por IA
 */
export function executarChecagens(e) {
  const out = [];
  const add = (regra, nivel, secao, mensagem, extra = {}) =>
    out.push({ id: `${regra}:${out.length}`, regra, nivel, secao, mensagem, ...extra });
  const c = e.criterios ?? {};
  const secoes = e.secoes ?? {};
  const orc = e.orcamento ?? [];
  const cron = e.cronograma ?? [];
  const mid = e.midia ?? [];
  const eq = e.equipe ?? [];
  const tituloDe = (id) => (e.specs ?? []).find((s) => s.id === id)?.titulo ?? id;
  const texto = (id) => secoes[id]?.rascunho ?? "";

  // 1) objetivo específico sem meta correspondente
  const objetivos = objetivosEspecificos(texto("objetivos"));
  const metas = metasDoTexto(texto("metas"));
  if (objetivos.length && metas.length) {
    if (e.ligacoes?.objetivos?.length) {
      for (const o of e.ligacoes.objetivos) if (!o.tem_meta) add("objetivo_sem_meta", "aviso", "metas", `O objetivo "${resumido(o.texto)}" não tem meta correspondente.`);
    } else {
      for (const o of objetivos) if (!temLigacao(o, metas)) add("objetivo_sem_meta", "aviso", "metas", `O objetivo "${resumido(o)}" não tem meta correspondente.`);
    }
  } else if (objetivos.length && texto("metas") === "") {
    add("objetivo_sem_meta", "nao_verificado", "metas", "Objetivos e metas: não verificado (a seção de metas ainda não foi escrita).");
  }

  // 2) meta sem atividade no cronograma
  if (metas.length && cron.length) {
    const atividades = cron.map((a) => a.atividade ?? "");
    for (const m of metas) if (!temLigacao(m, atividades)) add("meta_sem_atividade", "aviso", "cronograma", `A meta "${resumido(m)}" não aparece no cronograma.`);
  } else if (metas.length && !cron.length) {
    add("meta_sem_atividade", "nao_verificado", "cronograma", "Metas × cronograma: não verificado (o cronograma ainda está vazio).");
  }

  // 3) cronograma: prazo do edital e prestação de contas no fim
  if (cron.length) {
    const ultimo = Math.max(...cron.map((a) => Number(a.mes_fim) || 0));
    if (c.prazo_execucao_meses && ultimo > c.prazo_execucao_meses) {
      add("cronograma_acima_do_prazo", "erro", "cronograma", `O cronograma vai até o mês ${ultimo}, mas o edital permite ${c.prazo_execucao_meses} meses de execução.`);
    }
    const prestacao = cron.filter((a) => /presta[cç][aã]o de contas/i.test(a.atividade ?? ""));
    if (!prestacao.length) add("cronograma_sem_prestacao", "aviso", "cronograma", "Não há atividade de prestação de contas no cronograma.");
    else if (Math.max(...prestacao.map((a) => Number(a.mes_fim) || 0)) < ultimo) {
      add("prestacao_nao_e_a_ultima", "aviso", "cronograma", "A prestação de contas deveria ser a última atividade do cronograma.");
    }
  }

  // 4) orçamento × edital
  const r = resumoOrcamento(orc);
  const teto = Number(c.teto_orcamento) || Number(e.ficha?.edital_teto_por_projeto) || 0;
  if (teto > 0 && r.total > teto) add("total_acima_do_teto", "erro", "orcamento", `O total (${brl(r.total)}) passa do teto do edital (${brl(teto)}) em ${brl(r.total - teto)}.`);
  for (const [etapa, max] of Object.entries(c.percentuais_max ?? {})) {
    const b = r.porEtapa[etapa];
    if (b && r.total > 0 && b.percentual > Number(max) + 1e-9) {
      add("etapa_acima_do_limite", "erro", "orcamento", `${ROTULOS_ETAPA[etapa] ?? etapa} é ${pct(b.percentual)} do orçamento e o edital limita a ${pct(Number(max))}.`);
    }
  }
  for (const [etapa, min] of Object.entries(c.percentuais_min ?? {})) {
    const b = r.porEtapa[etapa];
    if (r.total > 0 && (b?.percentual ?? 0) < Number(min) - 1e-9) {
      add("etapa_abaixo_do_minimo", "erro", etapa === "acessibilidade" ? "acessibilidade" : "orcamento", `${ROTULOS_ETAPA[etapa] ?? etapa} é ${pct(b?.percentual ?? 0)} do orçamento e o edital pede no mínimo ${pct(Number(min))}.`);
    }
  }
  const vedadas = (c.rubricas_vedadas ?? []).map(semAcento).filter(Boolean);
  for (const i of orc) {
    const alvo = semAcento(`${i.rubrica ?? ""} ${i.item ?? ""}`);
    const v = vedadas.find((x) => alvo.includes(x));
    if (v) add("rubrica_vedada", "erro", "orcamento", `"${resumido(i.item ?? "")}" cai numa rubrica que o edital veda ("${v}").`, { itemId: i.id });
  }
  const semFonte = orc.filter((i) => !String(i.fonte_preco ?? "").trim());
  if (semFonte.length) add("item_sem_fonte", "aviso", "orcamento", `${semFonte.length} item(ns) do orçamento sem fonte de preço.`);
  const semJust = orc.filter((i) => !String(i.justificativa ?? "").trim());
  if (semJust.length) add("item_sem_justificativa", "aviso", "orcamento", `${semJust.length} item(ns) do orçamento sem justificativa.`);
  const aConfirmar = orc.filter((i) => totalDoItem(i) == null);
  if (aConfirmar.length) add("valor_a_confirmar", "aviso", "orcamento", `${aConfirmar.length} item(ns) com valor a confirmar: o total ainda não está completo.`);
  const declarado = Number(e.ficha?.custo_estimado) || 0;
  if (declarado > 0 && r.total > 0 && Math.abs(declarado - r.total) / declarado > 0.01) {
    add("total_diferente_da_ficha", "aviso", "orcamento", `O total do orçamento (${brl(r.total)}) difere do valor declarado na ficha (${brl(declarado)}).`);
  }

  // 5) acessibilidade citada precisa de custo
  const textoAcess = semAcento(texto("acessibilidade"));
  if (textoAcess) {
    const gastos = semAcento(orc.map((i) => `${i.item ?? ""} ${i.rubrica ?? ""} ${i.justificativa ?? ""}`).join(" "));
    for (const m of MEDIDAS_ACESSIBILIDADE) {
      if (m.chaves.some((k) => textoAcess.includes(k)) && !m.chaves.some((k) => gastos.includes(k))) {
        add("acessibilidade_sem_custo", "aviso", "acessibilidade", `A medida "${m.nome}" é citada em Acessibilidade, mas não tem custo no orçamento.`);
      }
    }
  }

  // 6) peça de divulgação paga sem custo no orçamento
  for (const p of mid) {
    const nome = p.peca ?? "peça";
    const custo = Number(p.custo) || 0;
    if (custo > 0) {
      const ligado = p.orcamento_item_id && orc.some((i) => i.id === p.orcamento_item_id);
      const igual = orc.some((i) => i.etapa === "divulgacao" && Math.abs((totalDoItem(i) ?? -1) - custo) < 0.01);
      if (!ligado && !igual) add("peca_paga_sem_custo", "aviso", "comunicacao", `A peça "${resumido(nome)}" tem custo de ${brl(custo)}, mas não está ligada a um item do orçamento.`, { itemId: p.id });
    } else if (CANAL_PAGO.test(`${p.canal ?? ""} ${nome}`)) {
      add("peca_paga_sem_custo", "aviso", "comunicacao", `A peça "${resumido(nome)}" parece paga, mas não tem custo informado.`, { itemId: p.id });
    }
  }

  // 7) equipe × orçamento
  const ligados = new Set(eq.flatMap((m) => m.orcamento_item_ids ?? []));
  for (const i of orc) {
    if (PESSOAL.test(i.rubrica ?? "") && !ligados.has(i.id)) {
      add("funcao_paga_sem_pessoa", "aviso", "equipe", `"${resumido(i.item ?? "")}" é pago no orçamento, mas não está ligado a ninguém da equipe.`, { itemId: i.id });
    }
  }
  for (const m of eq) {
    const sem = !(m.orcamento_item_ids ?? []).some((id) => orc.some((i) => i.id === id));
    if (sem) add("pessoa_sem_cache", "info", "equipe", `${resumido(m.nome_artistico_ou_funcao || m.funcao)} está na equipe, mas não tem cachê ligado no orçamento (ignore se for voluntário).`, { itemId: m.id });
  }

  // 8) contrapartida do edital citada
  if (c.contrapartida_exigida && texto("democratizacao") && !temLigacao(c.contrapartida_exigida, [texto("democratizacao")])) {
    add("contrapartida_nao_citada", "aviso", "democratizacao", "O edital exige contrapartida, mas ela não aparece no texto da seção Democratização.");
  }

  // 9) limites de caracteres e [COMPLETAR]
  for (const s of e.specs ?? []) {
    const t = texto(s.id);
    if (!t) continue;
    const limite = Number(c.limites_caracteres?.[s.id]) || s.limite_caracteres_padrao || 0;
    if (limite && t.length > limite) add("secao_acima_do_limite", "erro", s.id, `${s.titulo} tem ${t.length} caracteres e o limite é ${limite}. Corte ${t.length - limite}.`);
    const faltas = (t.match(/\[COMPLETAR:/g) ?? []).length;
    if (faltas) add("completar_pendente", "aviso", s.id, `${s.titulo}: ${faltas} ponto(s) [COMPLETAR] para preencher.`);
  }
  return out;
}

const PESO = { erro: 0, aviso: 1, info: 2, nao_verificado: 3 };
export const ordenarPendencias = (lista) => [...lista].sort((a, b) => PESO[a.nivel] - PESO[b.nivel]);
export { ETAPAS, valorAConfirmar };
