// Camada de tipos sobre os módulos JS compartilhados (tabelas.mjs, checagens.mjs). Eles são a fonte
// única das regras (o navegador usa os mesmos arquivos); aqui só se dá tipo para o Deno.
// @ts-ignore: módulos JS gerados por scripts/sincronizar_specs.mjs
import * as t from "./tabelas.mjs";
// @ts-ignore
import * as c from "./checagens.mjs";

export type Linha = Record<string, unknown>;
export type ResumoOrcamento = {
  total: number;
  semValor: number;
  itens: number;
  porEtapa: Record<string, { total: number; itens: number; percentual: number }>;
};
export type Pendencia = { id: string; regra: string; nivel: "erro" | "aviso" | "info" | "nao_verificado"; secao: string; mensagem: string; itemId?: string };

export const TABELAS = t.TABELAS as string[];
export const MAX_LINHAS = t.MAX_LINHAS as number;
export const ROTULOS_ETAPA = t.ROTULOS_ETAPA as Record<string, string>;
export const CAMPOS = t.CAMPOS as Record<string, unknown>;
export const lerNumero = t.lerNumero as (v: unknown) => number | null;
export const semAcento = t.semAcento as (v: unknown) => string;
export const totalDoItem = t.totalDoItem as (i: Linha) => number | null;
export const valorAConfirmar = t.valorAConfirmar as (i: Linha) => boolean;
export const resumoOrcamento = t.resumoOrcamento as (itens: Linha[]) => ResumoOrcamento;
export const normalizarLinha = t.normalizarLinha as unknown as (tabela: string, bruta: unknown) => { linha: Linha; erros: Record<string, string> };
export const executarChecagens = c.executarChecagens as (e: Record<string, unknown>) => Pendencia[];
