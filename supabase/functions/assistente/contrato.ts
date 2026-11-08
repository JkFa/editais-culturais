// Contrato da resposta do LLM e validação. O provedor recebe `SCHEMA_RESPOSTA` (modo JSON
// estruturado); mesmo assim tudo é revalidado aqui, porque o modelo pode errar.
import { fatosSeguros } from "./privacidade.ts";
import { TABELAS } from "./tipos_tabelas.ts";

export type Alerta = { nivel: "info" | "aviso"; texto: string };
/** Linhas que o LLM PROPÕE para uma tabela. Nada é gravado: o usuário aceita, edita ou descarta. */
export type ItensPropostos = { tabela: string; linhas: Array<Record<string, unknown>> };
export const MAX_LINHAS_PROPOSTAS = 40;
export type RespostaAssistente = {
  tipo: "pergunta" | "rascunho";
  perguntas: Array<{ id: string; texto: string }>;
  rascunho: string;
  alertas: Alerta[];
  fatos_novos_ficha: Record<string, unknown>;
  /** Só em seções estruturadas (orçamento, cronograma, mídia, equipe). */
  itens_propostos?: ItensPropostos | null;
};

export class ErroContrato extends Error {}

/**
 * Esquema enviado ao provedor (subconjunto portátil de JSON Schema). `fatos_novos_ficha` vai como
 * lista de pares porque alguns provedores não aceitam objeto sem propriedades fixas; a função
 * `validarResposta` devolve o objeto do contrato.
 */
export const SCHEMA_RESPOSTA = {
  type: "object",
  properties: {
    tipo: { type: "string", enum: ["pergunta", "rascunho"] },
    perguntas: {
      type: "array",
      items: { type: "object", properties: { id: { type: "string" }, texto: { type: "string" } }, required: ["id", "texto"] },
    },
    rascunho: { type: "string" },
    alertas: {
      type: "array",
      items: { type: "object", properties: { nivel: { type: "string", enum: ["info", "aviso"] }, texto: { type: "string" } }, required: ["nivel", "texto"] },
    },
    fatos_novos_ficha: {
      type: "array",
      items: { type: "object", properties: { chave: { type: "string" }, valor: { type: "string" } }, required: ["chave", "valor"] },
    },
    // Opcional: só nas seções estruturadas, junto com o rascunho. Todos os campos são opcionais.
    itens_propostos: {
      type: "object",
      properties: {
        tabela: { type: "string", enum: [...TABELAS] },
        linhas: {
          type: "array",
          items: {
            type: "object",
            properties: {
              etapa: { type: "string", enum: ["pre_producao", "producao", "pos_producao", "divulgacao", "acessibilidade", "administrativo"] },
              rubrica: { type: "string" },
              item: { type: "string" },
              unidade: { type: "string" },
              quantidade: { type: "number" },
              valor_unitario: { type: "number", nullable: true },
              fonte_preco: { type: "string", nullable: true },
              justificativa: { type: "string" },
              atividade: { type: "string" },
              mes_inicio: { type: "integer" },
              mes_fim: { type: "integer" },
              peca: { type: "string" },
              canal: { type: "string" },
              periodo: { type: "string" },
              custo: { type: "number", nullable: true },
              funcao: { type: "string" },
              nome_artistico_ou_funcao: { type: "string" },
              mini_curriculo: { type: "string" },
              local: { type: "string" },
            },
          },
        },
      },
      required: ["tabela", "linhas"],
    },
  },
  required: ["tipo", "perguntas", "rascunho", "alertas", "fatos_novos_ficha"],
} as const;

function lerItensPropostos(bruto: unknown): ItensPropostos | null {
  if (!bruto || typeof bruto !== "object" || Array.isArray(bruto)) return null;
  const { tabela, linhas } = bruto as { tabela?: unknown; linhas?: unknown };
  if (typeof tabela !== "string" || !TABELAS.includes(tabela) || !Array.isArray(linhas)) return null;
  const ok = linhas.filter((l) => l && typeof l === "object" && !Array.isArray(l)).slice(0, MAX_LINHAS_PROPOSTAS) as Array<Record<string, unknown>>;
  return ok.length ? { tabela, linhas: ok } : null;
}

const MAX_PERGUNTAS = 2;

function paraObjetoDeFatos(bruto: unknown): Record<string, unknown> {
  if (bruto == null) return {};
  if (Array.isArray(bruto)) {
    const obj: Record<string, unknown> = {};
    for (const par of bruto) {
      if (par && typeof par === "object" && typeof (par as { chave?: unknown }).chave === "string") {
        obj[(par as { chave: string }).chave] = (par as { valor?: unknown }).valor;
      }
    }
    return obj;
  }
  if (typeof bruto === "object") return bruto as Record<string, unknown>;
  throw new ErroContrato("fatos_novos_ficha deve ser objeto ou lista de pares");
}

/** Texto do modelo (às vezes embrulhado em ```json) → objeto do contrato, ou ErroContrato. */
export function lerJsonDoModelo(texto: string): unknown {
  const limpo = texto.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
  try {
    return JSON.parse(limpo);
  } catch {
    throw new ErroContrato("a resposta não é JSON válido");
  }
}

export function validarResposta(bruto: unknown): RespostaAssistente {
  if (!bruto || typeof bruto !== "object" || Array.isArray(bruto)) throw new ErroContrato("a resposta não é um objeto");
  const r = bruto as Record<string, unknown>;
  if (r.tipo !== "pergunta" && r.tipo !== "rascunho") throw new ErroContrato("tipo deve ser 'pergunta' ou 'rascunho'");

  const perguntas: RespostaAssistente["perguntas"] = [];
  for (const p of Array.isArray(r.perguntas) ? r.perguntas : []) {
    const q = p as { id?: unknown; texto?: unknown };
    if (typeof q?.id !== "string" || typeof q?.texto !== "string" || !q.texto.trim()) {
      throw new ErroContrato("pergunta sem id ou texto");
    }
    const id = q.id.trim().toLowerCase().replace(/[^a-z0-9_:-]+/g, "_").slice(0, 60);
    if (!id) throw new ErroContrato("pergunta com id vazio");
    perguntas.push({ id, texto: q.texto.trim().slice(0, 500) });
  }
  const rascunho = typeof r.rascunho === "string" ? r.rascunho.trim() : "";
  if (r.tipo === "pergunta" && perguntas.length === 0) throw new ErroContrato("tipo 'pergunta' sem perguntas");
  if (r.tipo === "rascunho" && !rascunho) throw new ErroContrato("tipo 'rascunho' sem texto");

  const alertas: Alerta[] = [];
  for (const a of Array.isArray(r.alertas) ? r.alertas : []) {
    const x = a as { nivel?: unknown; texto?: unknown };
    if ((x?.nivel !== "info" && x?.nivel !== "aviso") || typeof x?.texto !== "string") {
      throw new ErroContrato("alerta inválido");
    }
    if (x.texto.trim()) alertas.push({ nivel: x.nivel, texto: x.texto.trim().slice(0, 400) });
  }

  return {
    tipo: r.tipo,
    perguntas: r.tipo === "pergunta" ? perguntas.slice(0, MAX_PERGUNTAS) : [],
    rascunho: r.tipo === "rascunho" ? rascunho : "",
    alertas,
    fatos_novos_ficha: fatosSeguros(paraObjetoDeFatos(r.fatos_novos_ficha)),
    // Proposta malformada não derruba a resposta: o texto da seção continua valendo.
    itens_propostos: r.tipo === "rascunho" ? lerItensPropostos(r.itens_propostos) : null,
  };
}
