// Tabelas estruturadas do projeto (orçamento, cronograma, mídia, equipe).
// 1) `filtrarItensPropostos`: o que o LLM propôs só chega ao usuário depois destas regras. O LLM NUNCA
//    define preço sozinho: valor sem número dito pelo usuário vira nulo ("valor a confirmar").
// 2) `salvarItens`: a gravação é daqui (service_role). Revalida cada linha, confere o dono do projeto
//    e devolve os totais recalculados. O navegador não escreve nessas tabelas.
import type { ItensPropostos } from "./contrato.ts";
import { removerDadosPessoais } from "./privacidade.ts";
import type { Repositorio } from "./repositorio.ts";
import { CAMPOS, lerNumero, MAX_LINHAS, normalizarLinha, resumoOrcamento, semAcento } from "./tipos_tabelas.ts";
import type { Linha, ResumoOrcamento } from "./tipos_tabelas.ts";

export const FONTE_USUARIO = "informado pelo usuário";

/** Números que a pessoa escreveu: "2.000", "R$ 1.500,50", "2 mil", "1,5 mil". */
export function numerosDoTexto(textos: string[]): number[] {
  const achados: number[] = [];
  for (const t of textos) {
    for (const m of t.matchAll(/(\d+(?:[.,]\d+)?)\s*mil\b/gi)) {
      const n = lerNumero(m[1]);
      if (typeof n === "number" && !Number.isNaN(n)) achados.push(n * 1000);
    }
    for (const m of t.matchAll(/\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?/g)) {
      const n = lerNumero(m[0]);
      if (typeof n === "number" && !Number.isNaN(n)) achados.push(n);
    }
  }
  return achados;
}

const campoDePreco = (tabela: string) => (tabela === "orcamento_itens" ? "valor_unitario" : tabela === "midia_itens" ? "custo" : null);

export type ResultadoFiltro = { itens: ItensPropostos | null; precosRemovidos: number; descartadas: number };

/**
 * @param textosUsuario  só o que a PESSOA escreveu (respostas e mensagens), nunca texto do LLM.
 * @param tabelaEsperada tabela da seção; proposta para outra tabela é ignorada.
 */
export function filtrarItensPropostos(
  prop: ItensPropostos | null | undefined,
  o: { tabelaEsperada: string; etapaPadrao?: string; textosUsuario: string[] },
): ResultadoFiltro {
  if (!prop || prop.tabela !== o.tabelaEsperada) return { itens: null, precosRemovidos: 0, descartadas: 0 };
  const numeros = numerosDoTexto(o.textosUsuario);
  const textoUsuario = semAcento(o.textosUsuario.join(" "));
  const preco = campoDePreco(prop.tabela);
  const linhas: Array<Record<string, unknown>> = [];
  let precosRemovidos = 0;
  let descartadas = 0;
  for (const bruta of prop.linhas) {
    const copia: Record<string, unknown> = { ...bruta };
    delete copia.id; // a linha só ganha id quando o usuário a aceita
    for (const k of Object.keys(copia)) if (typeof copia[k] === "string") copia[k] = removerDadosPessoais(copia[k] as string).texto;
    if (o.etapaPadrao && (prop.tabela === "orcamento_itens" || prop.tabela === "cronograma_itens")) copia.etapa = o.etapaPadrao;
    if (preco) {
      const v = lerNumero(copia[preco]);
      const informado = typeof v === "number" && !Number.isNaN(v) && numeros.some((n) => Math.abs(n - v) < 0.005);
      if (!informado) {
        if (v != null) precosRemovidos++;
        copia[preco] = null;
        if (prop.tabela === "orcamento_itens") copia.fonte_preco = null;
      } else if (prop.tabela === "orcamento_itens") {
        const f = String(copia.fonte_preco ?? "").trim();
        // fonte só vale se a pessoa a mencionou; senão, "informado pelo usuário"
        copia.fonte_preco = f && semAcento(f).split(/\W+/).some((w) => w.length > 4 && textoUsuario.includes(w)) ? f : FONTE_USUARIO;
      }
    }
    if (prop.tabela === "equipe_membros") {
      const nome = String(copia.nome_artistico_ou_funcao ?? "").trim();
      // nome só vale se a pessoa o escreveu; senão fica a função (o LLM não inventa pessoas)
      if (!nome || !textoUsuario.includes(semAcento(nome))) copia.nome_artistico_ou_funcao = String(copia.funcao ?? "");
      copia.orcamento_item_ids = [];
    }
    if (prop.tabela === "midia_itens") copia.orcamento_item_id = null;
    const { linha, erros } = normalizarLinha(prop.tabela, copia);
    if (Object.keys(erros).length) {
      descartadas++;
      continue;
    }
    delete linha.id;
    linhas.push(linha);
  }
  return { itens: linhas.length ? { tabela: prop.tabela, linhas } : null, precosRemovidos, descartadas };
}

// ── gravação ─────────────────────────────────────────────────────────────────────────────────

export type ErroLinha = { indice: number; campos: Record<string, string> };
export type ResultadoSalvar =
  | { ok: true; tabela: string; linhas: Array<Record<string, unknown>>; resumo?: ResumoOrcamento; avisos: string[] }
  | { ok: false; codigo: "tabela" | "linhas" | "validacao" | "id_conflito"; mensagem: string; erros?: ErroLinha[] };

export async function salvarItens(
  repo: Repositorio,
  o: { projetoId: string; tabela: unknown; linhas: unknown },
): Promise<ResultadoSalvar> {
  const tabela = typeof o.tabela === "string" ? o.tabela : "";
  if (!Object.hasOwn(CAMPOS, tabela)) return { ok: false, codigo: "tabela", mensagem: "Tabela inválida." };
  if (!Array.isArray(o.linhas) || o.linhas.length > MAX_LINHAS) {
    return { ok: false, codigo: "linhas", mensagem: `Envie uma lista com até ${MAX_LINHAS} linhas.` };
  }
  const avisos: string[] = [];
  const erros: ErroLinha[] = [];
  const linhas: Array<Record<string, unknown>> = [];
  o.linhas.forEach((bruta, indice) => {
    const copia: Record<string, unknown> = bruta && typeof bruta === "object" ? { ...(bruta as Record<string, unknown>) } : {};
    let limpou = false;
    for (const k of Object.keys(copia)) {
      if (typeof copia[k] === "string") {
        const r = removerDadosPessoais(copia[k] as string);
        if (r.removidos.length) limpou = true;
        copia[k] = r.texto;
      }
    }
    if (limpou && !avisos.length) avisos.push("Tirei dados pessoais (CPF, CNPJ, e-mail, telefone ou CEP) do texto. Nesta tabela não guardamos documentos nem contatos.");
    const { linha, erros: e } = normalizarLinha(tabela, copia);
    if (Object.keys(e).length) erros.push({ indice, campos: e });
    else linhas.push(linha);
  });
  if (erros.length) return { ok: false, codigo: "validacao", mensagem: "Há linhas com problema. Corrija e salve de novo.", erros };

  // ids: os que o navegador gerou (uuid) são mantidos, os outros nascem aqui
  const comId: Linha[] = linhas.map((l, i) => ({ ...l, id: (l.id as string | undefined) ?? crypto.randomUUID(), ordem: i }));
  if (new Set(comId.map((l) => l.id)).size !== comId.length) return { ok: false, codigo: "id_conflito", mensagem: "Há linhas repetidas." };
  const conflito = await repo.idsDeOutroProjeto(tabela, o.projetoId, comId.map((l) => l.id as string));
  if (conflito.length) return { ok: false, codigo: "id_conflito", mensagem: "Algumas linhas pertencem a outro projeto." };

  // referências entre tabelas só valem para itens de orçamento DESTE projeto
  if (tabela === "midia_itens" || tabela === "equipe_membros") {
    const validos = new Set(await repo.idsDoOrcamento(o.projetoId));
    for (const l of comId) {
      if (tabela === "midia_itens" && l.orcamento_item_id && !validos.has(l.orcamento_item_id as string)) {
        l.orcamento_item_id = null;
        avisos.push("Uma peça apontava para um item de orçamento que não existe mais; tirei a ligação.");
      }
      if (tabela === "equipe_membros") l.orcamento_item_ids = (l.orcamento_item_ids as string[]).filter((id) => validos.has(id));
    }
  }
  await repo.substituirItens(tabela, o.projetoId, comId);
  if (tabela === "orcamento_itens") await repo.limparReferenciasAoOrcamento(o.projetoId, comId.map((l) => l.id as string));
  return { ok: true, tabela, linhas: comId, resumo: tabela === "orcamento_itens" ? resumoOrcamento(comId) : undefined, avisos: [...new Set(avisos)] };
}
