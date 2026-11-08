// Regras puras das tabelas estruturadas do projeto (orçamento, cronograma, mídia e equipe).
// Compartilhado pelo navegador (editores) e pela Edge Function (que revalida e recalcula antes de
// gravar). Fonte única em specs/lib/; `node scripts/sincronizar_specs.mjs` copia para os destinos.

export const ETAPAS = ["pre_producao", "producao", "pos_producao", "divulgacao", "acessibilidade", "administrativo"];
export const ROTULOS_ETAPA = {
  pre_producao: "Pré-produção",
  producao: "Produção",
  pos_producao: "Pós-produção",
  divulgacao: "Divulgação",
  acessibilidade: "Acessibilidade",
  administrativo: "Administrativo",
};
export const MAX_LINHAS = 300;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Descrição dos campos aceitos (tudo fora daqui é descartado). t: texto | num | int | enum | uuid | uuids. */
export const CAMPOS = {
  orcamento_itens: {
    etapa: { t: "enum", valores: ETAPAS, obrigatorio: true },
    rubrica: { t: "texto", max: 120, padrao: "" },
    item: { t: "texto", max: 200, obrigatorio: true },
    unidade: { t: "texto", max: 40, padrao: "unidade" },
    quantidade: { t: "num", min: 0, max: 1e9, padrao: 1 },
    valor_unitario: { t: "num", min: 0, max: 1e12, anulavel: true },
    fonte_preco: { t: "texto", max: 300, anulavel: true },
    justificativa: { t: "texto", max: 600, anulavel: true },
  },
  cronograma_itens: {
    etapa: { t: "enum", valores: ETAPAS, obrigatorio: true },
    atividade: { t: "texto", max: 200, obrigatorio: true },
    mes_inicio: { t: "int", min: 1, max: 120, obrigatorio: true },
    mes_fim: { t: "int", min: 1, max: 120, obrigatorio: true },
  },
  midia_itens: {
    peca: { t: "texto", max: 200, obrigatorio: true },
    canal: { t: "texto", max: 120, padrao: "" },
    periodo: { t: "texto", max: 120, padrao: "" },
    quantidade: { t: "num", min: 0, max: 1e9, padrao: 1 },
    custo: { t: "num", min: 0, max: 1e12, anulavel: true },
    orcamento_item_id: { t: "uuid", anulavel: true },
  },
  equipe_membros: {
    funcao: { t: "texto", max: 120, obrigatorio: true },
    nome_artistico_ou_funcao: { t: "texto", max: 160, padrao: "" },
    mini_curriculo: { t: "texto", max: 900, padrao: "" },
    local: { t: "texto", max: 120, padrao: "" },
    orcamento_item_ids: { t: "uuids", padrao: [] },
  },
};
export const TABELAS = Object.keys(CAMPOS);

/** "1.500,50" → 1500.5; "2000" → 2000; vazio → null; lixo → NaN. */
export function lerNumero(v) {
  if (v == null || v === "") return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : NaN;
  let t = String(v).trim().replace(/^R\$\s*/i, "").replace(/\s/g, "");
  if (!t) return null;
  if (t.includes(",")) t = t.replace(/\./g, "").replace(",", ".");
  else if (/^\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, "");
  const n = Number(t);
  return Number.isFinite(n) ? n : NaN;
}

const arredonda2 = (x) => Math.round((x + Number.EPSILON) * 100) / 100;

function normalizarCampo(nome, d, valor) {
  const vazio = valor == null || (typeof valor === "string" && valor.trim() === "");
  if (vazio) {
    if (d.obrigatorio) return { erro: "Preencha este campo." };
    if (d.anulavel) return { valor: null };
    return { valor: d.padrao ?? (d.t === "texto" ? "" : null) };
  }
  switch (d.t) {
    case "texto": {
      const t = String(valor).replace(/\s+/g, " ").trim();
      if (t.length > d.max) return { erro: `Use até ${d.max} caracteres.` };
      return { valor: t };
    }
    case "num":
    case "int": {
      const n = lerNumero(valor);
      if (n == null || Number.isNaN(n)) return { erro: "Digite um número." };
      if (d.t === "int" && !Number.isInteger(n)) return { erro: "Use um número inteiro." };
      if (n < d.min) return { erro: `O mínimo é ${d.min}.` };
      if (n > d.max) return { erro: "Número grande demais." };
      return { valor: d.t === "int" ? n : Math.round(n * 1000) / 1000 };
    }
    case "enum":
      return d.valores.includes(valor) ? { valor } : { erro: "Escolha uma das opções." };
    case "uuid":
      return UUID.test(String(valor)) ? { valor: String(valor).toLowerCase() } : { erro: "Referência inválida." };
    case "uuids": {
      if (!Array.isArray(valor)) return { erro: "Lista inválida." };
      const ok = valor.filter((x) => UUID.test(String(x))).map((x) => String(x).toLowerCase());
      return { valor: [...new Set(ok)].slice(0, 50) };
    }
    default:
      return { erro: "Campo desconhecido." };
  }
}

/**
 * Linha bruta → { linha, erros }. Só os campos da tabela passam (`projeto_id`, `id` de outro
 * projeto e qualquer outra coisa são descartados). `id` só é mantido se for um UUID.
 */
export function normalizarLinha(tabela, bruta) {
  const campos = CAMPOS[tabela];
  if (!campos) throw new Error(`tabela desconhecida: ${tabela}`);
  const origem = bruta && typeof bruta === "object" ? bruta : {};
  const linha = {};
  const erros = {};
  for (const [nome, d] of Object.entries(campos)) {
    const r = normalizarCampo(nome, d, origem[nome]);
    if (r.erro) erros[nome] = r.erro;
    else linha[nome] = r.valor;
  }
  if (typeof origem.id === "string" && UUID.test(origem.id)) linha.id = origem.id.toLowerCase();
  if (tabela === "cronograma_itens" && !erros.mes_inicio && !erros.mes_fim && linha.mes_fim < linha.mes_inicio) {
    erros.mes_fim = "O mês final não pode vir antes do inicial.";
  }
  return { linha, erros };
}

// ── orçamento ────────────────────────────────────────────────────────────────────────────────

/** Quantidade × valor unitário, em reais com 2 casas. `null` quando o valor ainda é "a confirmar". */
export function totalDoItem(item) {
  const q = lerNumero(item.quantidade);
  const v = lerNumero(item.valor_unitario);
  if (v == null || Number.isNaN(v) || q == null || Number.isNaN(q)) return null;
  return arredonda2(q * v);
}

/** O item só conta como confirmado se tem valor E fonte do preço. */
export const valorAConfirmar = (item) => totalDoItem(item) == null || !String(item.fonte_preco ?? "").trim();

export function resumoOrcamento(itens) {
  const porEtapa = Object.fromEntries(ETAPAS.map((e) => [e, { total: 0, itens: 0, percentual: 0 }]));
  let total = 0;
  let semValor = 0;
  for (const i of itens ?? []) {
    const t = totalDoItem(i);
    const bloco = porEtapa[i.etapa] ?? (porEtapa[i.etapa] = { total: 0, itens: 0, percentual: 0 });
    bloco.itens += 1;
    if (t == null) semValor += 1;
    else {
      bloco.total = arredonda2(bloco.total + t);
      total = arredonda2(total + t);
    }
  }
  for (const b of Object.values(porEtapa)) b.percentual = total > 0 ? b.total / total : 0;
  return { total, porEtapa, semValor, itens: (itens ?? []).length };
}

const semAcento = (t) => String(t ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
export { semAcento };
