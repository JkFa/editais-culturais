// Conversão entre o registro de `edital_criterios` e o formulário da revisão (puro, testável sem navegador).
import { ETAPAS, lerNumero } from "../projeto/tabelas.mjs";

export const SECOES = [
  ["ficha", "Ficha do projeto"], ["apresentacao", "Apresentação / Resumo"], ["justificativa", "Justificativa"],
  ["objetivos", "Objetivos"], ["metas", "Metas e indicadores"], ["publico", "Público-alvo"],
  ["acessibilidade", "Acessibilidade"], ["democratizacao", "Democratização do acesso"], ["comunicacao", "Plano de comunicação"],
  ["equipe", "Equipe"], ["cronograma", "Cronograma"], ["orcamento", "Orçamento"],
];
export { ETAPAS };

const txt = (n) => (n == null ? "" : String(n).replace(".", ","));
const pct = (f) => (f == null ? "" : txt(Math.round(Number(f) * 10000) / 100));

/** Registro do banco → valores dos campos do formulário (tudo texto, como a pessoa vê). */
export function paraFormulario(r) {
  return {
    teto_orcamento: txt(r.teto_orcamento),
    teto_por_proponente: txt(r.teto_por_proponente),
    percentuais_max: Object.fromEntries(ETAPAS.map((e) => [e, pct(r.percentuais_max?.[e])])),
    percentuais_min: Object.fromEntries(ETAPAS.map((e) => [e, pct(r.percentuais_min?.[e])])),
    rubricas_vedadas: (r.rubricas_vedadas ?? []).join("\n"),
    limites_caracteres: Object.fromEntries(SECOES.map(([id]) => [id, txt(r.limites_caracteres?.[id])])),
    campos_exigidos: (r.campos_exigidos ?? []).map((c) => ({ nome: c.nome ?? "", secao: c.secao ?? "" })),
    contrapartida_exigida: r.contrapartida_exigida ?? "",
    prazo_execucao_meses: txt(r.prazo_execucao_meses),
    exige_acessibilidade: r.exige_acessibilidade == null ? "" : r.exige_acessibilidade ? "sim" : "nao",
    observacoes: r.observacoes ?? "",
    criterios: (r.criterios ?? []).map((c) => ({
      id: c.id ?? "", nome: c.nome ?? "", descricao: c.descricao ?? "", peso: txt(c.peso), nota_max: txt(c.nota_max),
      trecho_edital: c.trecho_edital ?? "", pagina: c.pagina ?? null,
    })),
  };
}

const vazio = (t) => t == null || String(t).trim() === "";

function numero(texto, rotulo, { min = 0, max = Infinity, inteiro = false, exclusivoMin = false } = {}, erros) {
  if (vazio(texto)) return null;
  const n = lerNumero(texto);
  if (n == null || Number.isNaN(n)) return void (erros[rotulo] = "Digite um número.");
  if (inteiro && !Number.isInteger(n)) return void (erros[rotulo] = "Use um número inteiro.");
  if (exclusivoMin ? n <= min : n < min) return void (erros[rotulo] = exclusivoMin ? `Use um valor maior que ${min}.` : `O mínimo é ${min}.`);
  if (n > max) return void (erros[rotulo] = `O máximo é ${max}.`);
  return n;
}

/** Formulário → colunas do banco (com as mesmas regras das checagens do banco) ou { erros }. */
export function deFormulario(f) {
  const erros = {};
  const percentuais = (grupo, rotulo) => {
    const saida = {};
    for (const e of ETAPAS) {
      const v = numero(f[grupo][e], `${rotulo}.${e}`, { min: 0, max: 100, exclusivoMin: true }, erros);
      if (v != null) saida[e] = Math.round((v / 100) * 10000) / 10000;
    }
    return saida;
  };
  const limites = {};
  for (const [id] of SECOES) {
    const v = numero(f.limites_caracteres[id], `limites.${id}`, { min: 100, max: 20000, inteiro: true }, erros);
    if (v != null) limites[id] = v;
  }
  const criterios = f.criterios.map((c, i) => {
    const nome = String(c.nome ?? "").trim();
    if (nome.length < 3) erros[`criterios.${i}.nome`] = "Dê um nome ao critério.";
    return {
      id: c.id || `criterio_${i + 1}`, nome, descricao: vazio(c.descricao) ? null : String(c.descricao).trim().slice(0, 400),
      peso: numero(c.peso, `criterios.${i}.peso`, { min: 0, exclusivoMin: true }, erros),
      nota_max: numero(c.nota_max, `criterios.${i}.nota_max`, { min: 0, exclusivoMin: true }, erros),
      trecho_edital: c.trecho_edital ?? "", pagina: c.pagina ?? null,
    };
  });
  const campos = f.campos_exigidos.filter((c) => !vazio(c.nome)).map((c) => ({ nome: String(c.nome).trim(), secao: c.secao || null }));
  const patch = {
    teto_orcamento: numero(f.teto_orcamento, "teto_orcamento", { min: 0, exclusivoMin: true }, erros),
    teto_por_proponente: numero(f.teto_por_proponente, "teto_por_proponente", { min: 0, exclusivoMin: true }, erros),
    percentuais_max: percentuais("percentuais_max", "percentuais_max"),
    percentuais_min: percentuais("percentuais_min", "percentuais_min"),
    rubricas_vedadas: String(f.rubricas_vedadas ?? "").split("\n").map((x) => x.trim()).filter(Boolean),
    limites_caracteres: limites,
    campos_exigidos: campos,
    contrapartida_exigida: vazio(f.contrapartida_exigida) ? null : String(f.contrapartida_exigida).trim(),
    prazo_execucao_meses: numero(f.prazo_execucao_meses, "prazo_execucao_meses", { min: 1, max: 120, inteiro: true }, erros),
    exige_acessibilidade: f.exige_acessibilidade === "sim" ? true : f.exige_acessibilidade === "nao" ? false : null,
    observacoes: vazio(f.observacoes) ? null : String(f.observacoes).trim().slice(0, 600),
    criterios,
  };
  return Object.keys(erros).length ? { erros } : { patch };
}

/** O que o botão "Sem critérios aproveitáveis" grava: tudo vazio, conferido, com a observação. */
export function patchSemCriterios() {
  return {
    criterios: [], teto_orcamento: null, teto_por_proponente: null, percentuais_max: {}, percentuais_min: {}, rubricas_vedadas: [],
    limites_caracteres: {}, campos_exigidos: [], contrapartida_exigida: null, prazo_execucao_meses: null, exige_acessibilidade: null,
    observacoes: "Conferido por uma pessoa: o documento não traz critérios aproveitáveis.",
  };
}

/** Marca de conferência (feita no navegador do administrador; a RLS só deixa um administrador gravar). */
export const marcaDeRevisao = (userId, agora = new Date()) => ({ revisado: true, revisado_em: agora.toISOString(), revisado_por: userId });

/** Link para o documento na página da evidência (PDF abre na página; página HTML ignora o fragmento). */
export function linkDoDocumento(fonteUrl, pagina) {
  if (!fonteUrl) return null;
  return pagina && /\.pdf(\?|$)/i.test(fonteUrl) && !fonteUrl.includes("#") ? `${fonteUrl}#page=${pagina}` : fonteUrl;
}

/** Evidências do registro numa lista legível: [{campo, rotulo, trecho, pagina}]. */
export function evidenciasLegiveis(r) {
  const rotulos = {
    teto_orcamento: "Teto por projeto", teto_por_proponente: "Teto por proponente", contrapartida_exigida: "Contrapartida",
    prazo_execucao_meses: "Prazo de execução", exige_acessibilidade: "Exige acessibilidade",
  };
  const saida = Object.entries(r.evidencias ?? {}).map(([campo, e]) => {
    const [base, resto] = campo.split(".");
    const nome = rotulos[campo] ?? ({ percentuais_max: `% máximo (${resto})`, percentuais_min: `% mínimo (${resto})`, limites_caracteres: `Limite de caracteres (${resto})`, rubricas_vedadas: `Rubrica vedada (${resto})`, campos_exigidos: `Campo exigido nº ${Number(resto) + 1}` }[base] ?? campo);
    return { campo, rotulo: nome, trecho: e.trecho, pagina: e.pagina ?? null };
  });
  for (const c of r.criterios ?? []) saida.push({ campo: `criterio.${c.id}`, rotulo: `Critério: ${c.nome}`, trecho: c.trecho_edital, pagina: c.pagina ?? null });
  return saida;
}
