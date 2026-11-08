import { ETAPAS, ROTULOS_ETAPA, resumoOrcamento, totalDoItem } from "./tabelas.mjs";

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

// ── Fase 2: campos do edital, tabelas em texto e tabelas-resumo ────────────────────────────────

/**
 * Campos para "copiar por campo". Com `campos_exigidos` do edital (lista ordenada {nome, secao}), segue a ordem
 * e os nomes do edital; as seções que o edital não menciona vêm depois. Sem isso, a ordem do assistente.
 * O limite de caracteres é o do edital para aquela seção quando existir (senão, o padrão do spec).
 */
export function camposParaCopiar({ secoes, linhaDe, criterios }) {
  const porId = new Map(secoes.map((s) => [s.id, s]));
  const limiteDe = (s) => Number(criterios?.limites_caracteres?.[s.id]) || s.limite_caracteres_padrao;
  const montar = (nome, s) => {
    const l = s ? linhaDe(s.id) : null;
    const texto = l?.rascunho ?? "";
    return { nome, secaoId: s?.id ?? null, texto, limite: s ? limiteDe(s) : null, aprovada: Boolean(l?.aprovado), semSecao: !s };
  };
  const exigidos = Array.isArray(criterios?.campos_exigidos) ? criterios.campos_exigidos.filter((c) => c?.nome) : [];
  if (!exigidos.length) return { doEdital: false, campos: secoes.map((s) => montar(s.titulo, s)) };
  const usadas = new Set();
  const campos = exigidos.map((c) => {
    const s = c.secao ? porId.get(c.secao) : null;
    if (s) usadas.add(s.id);
    return montar(c.nome, s);
  });
  const outras = secoes.filter((s) => !usadas.has(s.id)).map((s) => ({ ...montar(s.titulo, s), extra: true }));
  return { doEdital: true, campos: [...campos, ...outras] };
}

const numBr = (n) => (n == null || Number.isNaN(Number(n)) ? "" : String(n).replace(".", ","));

/** Tabela como texto separado por tabulação (cola direto numa planilha ou numa plataforma de inscrição). */
export function tabelaParaTexto(tabela, linhas) {
  const linhasTsv = {
    orcamento_itens: [["Etapa", "Rubrica", "Item", "Unidade", "Qtd", "Valor unitário", "Total", "Fonte do preço", "Justificativa"],
      ...linhas.map((i) => [ROTULOS_ETAPA[i.etapa] ?? i.etapa, i.rubrica, i.item, i.unidade, numBr(i.quantidade), numBr(i.valor_unitario), numBr(totalDoItem(i)), i.fonte_preco, i.justificativa])],
    cronograma_itens: [["Atividade", "Etapa", "Mês inicial", "Mês final"], ...linhas.map((a) => [a.atividade, ROTULOS_ETAPA[a.etapa] ?? a.etapa, a.mes_inicio, a.mes_fim])],
    midia_itens: [["Peça", "Canal", "Período", "Quantidade", "Custo"], ...linhas.map((p) => [p.peca, p.canal, p.periodo, numBr(p.quantidade), numBr(p.custo)])],
    equipe_membros: [["Função", "Nome artístico", "Cidade ou região", "Mini currículo"], ...linhas.map((m) => [m.funcao, m.nome_artistico_ou_funcao, m.local, m.mini_curriculo])],
  }[tabela];
  if (!linhasTsv) return "";
  return linhasTsv.map((l) => l.map((c) => String(c ?? "").replace(/[\t\r\n]+/g, " ").trim()).join("\t")).join("\n");
}

const moeda = (n) => `R$ ${Number(n ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** Linhas das duas tabelas-resumo do .docx (texto puro; o arquivo as desenha). */
export function resumosParaDocx({ orcamento = [], cronograma = [] }) {
  const out = { orcamento: null, cronograma: null };
  const r = resumoOrcamento(orcamento);
  if (orcamento.length) {
    const etapas = ETAPAS.filter((e) => r.porEtapa[e].itens);
    out.orcamento = {
      cabecalho: ["Etapa", "Total", "% do total"],
      linhas: [
        ...etapas.map((e) => [ROTULOS_ETAPA[e], moeda(r.porEtapa[e].total), `${(r.porEtapa[e].percentual * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`]),
        ["Total geral", moeda(r.total), "100%"],
      ],
      aviso: r.semValor ? `${r.semValor} item(ns) ainda sem valor: o total está incompleto.` : null,
    };
  }
  if (cronograma.length) {
    out.cronograma = {
      cabecalho: ["Atividade", "Etapa", "Período"],
      linhas: cronograma.map((a) => [a.atividade, ROTULOS_ETAPA[a.etapa] ?? a.etapa, Number(a.mes_inicio) === Number(a.mes_fim) ? `mês ${a.mes_inicio}` : `meses ${a.mes_inicio} a ${a.mes_fim}`]),
    };
  }
  return out;
}
