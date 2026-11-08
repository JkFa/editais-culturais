// Utilidades só dos testes: specs reais, projeto com dados pessoais plantados, repositório e LLM falsos.
import type { RespostaAssistente } from "./contrato.ts";
import type { ProvedorLlm } from "./llm.ts";
import type { ContextoSecao, Projeto, Repositorio, TabelasDoProjeto, Turno } from "./repositorio.ts";
import type { Specs } from "./handler.ts";
import specsJson from "./specs.gen.json" with { type: "json" };
// @ts-ignore: módulo JS compartilhado
import { pendentes, perguntasAplicaveis } from "./secoes.mjs";
import type { EntradaPrompt, SpecSecao } from "./prompts.ts";

export const specs = specsJson as unknown as Specs;
export const spec = (id: string) => specs.secoes.find((s) => s.id === id) as unknown as SpecSecao;

/** Dados pessoais que NÃO podem aparecer em nenhum prompt. */
export const PII = ["123.456.789-09", "12.345.678/0001-95", "ana.souza@exemplo.com", "(81) 99999-1234", "50000-123", "pessoa negra", "pessoa com deficiência", "Rua das Flores"];

export const fichaComPii = {
  tipo_proponente: "MEI",
  linguagem: "Teatro",
  cidades: ["Recife"],
  custo_estimado: 80000,
  territorio_prioritario: true,
  cpf: PII[0],
  cnpj: PII[1],
  email: PII[2],
  telefone: PII[3],
  endereco: "Rua das Flores, 10",
  nome_completo: "Ana Souza",
  cotas: [PII[5], PII[6]],
  ideia: `Oficina de teatro de rua. Contato ana.souza@exemplo.com ou (81) 99999-1234.`,
};

export function entradaBase(id: string, extra: Partial<EntradaPrompt> = {}): EntradaPrompt {
  const s = spec(id);
  const ctx = { ficha: fichaComPii };
  return {
    acao: "responder",
    spec: s,
    aplicaveis: perguntasAplicaveis(s, ctx, { canal: "chat" }),
    pendentes: pendentes(s, ctx, new Set()).map((p: { id: string }) => p.id),
    ficha: fichaComPii,
    edital: { titulo: "Edital de Fomento 001/2026 - PNAB Recife" },
    criterios: null,
    aprovadas: [],
    respostas: [],
    historico: [],
    limite: s.limite_caracteres_padrao,
    checagensCatalogo: specs.checagens,
    aprofundamentos: {},
    ...extra,
  };
}

export const projetoPadrao = (extra: Partial<Projeto> = {}): Projeto => ({
  id: "11111111-1111-4111-8111-111111111111",
  user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  titulo: "Oficina de Teatro de Rua",
  fluxo: "padrao",
  ficha: { tipo_proponente: "MEI", cidades: ["Recife"] },
  edital_id: null,
  edital_titulo: "Edital 001/2026",
  ...extra,
});

export type Chamada = { prompt: string; sistema: string };

export function llmFalso(respostas: Array<string | Error | RespostaAssistente | Record<string, unknown>>) {
  const chamadas: Chamada[] = [];
  const provedor: ProvedorLlm = {
    gerar(prompt, o) {
      chamadas.push({ prompt, sistema: o.sistema });
      const r = respostas[Math.min(chamadas.length - 1, respostas.length - 1)];
      if (r instanceof Error) return Promise.reject(r);
      return Promise.resolve({ texto: typeof r === "string" ? r : JSON.stringify(r), modelo: "falso" });
    },
  };
  return { provedor, chamadas };
}

export const perguntaOk = { tipo: "pergunta", perguntas: [{ id: "tipo_proponente", texto: "Como vai se inscrever?" }], rascunho: "", alertas: [], fatos_novos_ficha: [] };
export const rascunhoOk = { tipo: "rascunho", perguntas: [], rascunho: "A oficina será realizada em Recife.", alertas: [], fatos_novos_ficha: [] };

export function repoFalso(o: {
  usuario?: string | null;
  projeto?: Projeto | null;
  contexto?: Partial<ContextoSecao>;
  usados?: number;
  criterios?: Record<string, unknown> | null;
  tabelas?: Partial<TabelasDoProjeto>;
  textosDoUsuario?: string[];
  /** Ids que "já existem em outro projeto" (para testar o bloqueio entre projetos). */
  idsDeOutros?: string[];
  idsOrcamento?: string[];
} = {}) {
  const salvos: Turno[] = [];
  const consumos: number[] = [];
  const gravacoes: Array<{ tabela: string; projetoId: string; linhas: Array<Record<string, unknown>> }> = [];
  const limpezas: Array<{ projetoId: string; ids: string[] }> = [];
  const repo: Repositorio = {
    usuarioDoToken: () => Promise.resolve(o.usuario === undefined ? projetoPadrao().user_id : o.usuario),
    carregarProjeto: () => Promise.resolve(o.projeto === undefined ? projetoPadrao() : o.projeto),
    carregarSecao: () => Promise.resolve({ respostas: [], aprovadas: [], secao: null, historico: [], ...o.contexto }),
    carregarCriterios: () => Promise.resolve((o.criterios ?? null) as never),
    carregarTabelas: () => Promise.resolve({ orcamento_itens: [], cronograma_itens: [], midia_itens: [], equipe_membros: [], ...o.tabelas }),
    carregarTextosDoUsuario: () => Promise.resolve(o.textosDoUsuario ?? []),
    idsDeOutroProjeto: (_t, _p, ids) => Promise.resolve(ids.filter((i) => (o.idsDeOutros ?? []).includes(i))),
    idsDoOrcamento: () => Promise.resolve(o.idsOrcamento ?? []),
    substituirItens: (tabela, projetoId, linhas) => {
      gravacoes.push({ tabela, projetoId, linhas });
      return Promise.resolve();
    },
    limparReferenciasAoOrcamento: (projetoId, ids) => {
      limpezas.push({ projetoId, ids });
      return Promise.resolve();
    },
    consumirUso: (_u, limite) => {
      consumos.push(limite);
      return Promise.resolve(o.usados ?? 1);
    },
    salvarTurno: (t) => {
      salvos.push(t);
      return Promise.resolve();
    },
  };
  return { repo, salvos, consumos, gravacoes, limpezas };
}
