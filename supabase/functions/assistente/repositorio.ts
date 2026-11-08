// Acesso ao banco por PostgREST (fetch puro, como o resto do projeto).
// Leituras usam o JWT do usuário (a RLS garante que só vê o que é dele). Escritas usam a
// service_role, porque mensagens, respostas e uso diário não têm policy de escrita para o usuário.
// Esta função NUNCA consulta `projeto_proponente` (dados pessoais).
import type { RespostaAssistente } from "./contrato.ts";
import type { Criterios } from "./prompts.ts";

export type Projeto = {
  id: string;
  user_id: string;
  titulo: string;
  fluxo: "padrao" | "audiovisual";
  ficha: Record<string, unknown>;
  edital_id: number | null;
  edital_titulo: string | null;
};
export type ContextoSecao = {
  respostas: Array<{ pergunta_id: string; resposta: string }>;
  aprovadas: Array<{ secao_id: string; rascunho: string }>;
  secao: { rascunho: string; aprovado: boolean } | null;
  historico: Array<{ papel: "usuario" | "assistente"; conteudo: Record<string, unknown> }>;
};
export type TabelasDoProjeto = {
  orcamento_itens: Array<Record<string, unknown>>;
  cronograma_itens: Array<Record<string, unknown>>;
  midia_itens: Array<Record<string, unknown>>;
  equipe_membros: Array<Record<string, unknown>>;
};
export const TABELAS_ESTRUTURADAS = ["orcamento_itens", "cronograma_itens", "midia_itens", "equipe_membros"] as const;
export type Turno = {
  userId: string;
  projetoId: string;
  secaoId: string;
  mensagemUsuario?: string;
  respostas: Array<{ pergunta_id: string; resposta: string }>;
  resposta: RespostaAssistente;
  ficha: Record<string, unknown>;
  reabrir: boolean;
};

export interface Repositorio {
  usuarioDoToken(jwt: string): Promise<string | null>;
  carregarProjeto(jwt: string, projetoId: string): Promise<Projeto | null>;
  carregarSecao(jwt: string, projetoId: string, secaoId: string, dependencias: string[]): Promise<ContextoSecao>;
  carregarCriterios(jwt: string, editalId: number | null): Promise<Criterios>;
  /** Linhas do orçamento, cronograma, mídia e equipe (lidas com o JWT do usuário: a RLS filtra). */
  carregarTabelas(jwt: string, projetoId: string): Promise<TabelasDoProjeto>;
  /** Tudo o que a PESSOA escreveu nas respostas do projeto (para conferir números e nomes propostos pelo LLM). */
  carregarTextosDoUsuario(jwt: string, projetoId: string): Promise<string[]>;
  /** Ids (dos informados) que já existem em OUTRO projeto. */
  idsDeOutroProjeto(tabela: string, projetoId: string, ids: string[]): Promise<string[]>;
  idsDoOrcamento(projetoId: string): Promise<string[]>;
  /** Grava as linhas do projeto (upsert por id) e apaga as que sumiram. Linhas já trazem projeto_id? Não: é posto aqui. */
  substituirItens(tabela: string, projetoId: string, linhas: Array<Record<string, unknown>>): Promise<void>;
  /** Tira da equipe as ligações com itens de orçamento que não existem mais. */
  limparReferenciasAoOrcamento(projetoId: string, idsValidos: string[]): Promise<void>;
  /** Total de usos hoje, ou -1 se o limite já foi atingido. */
  consumirUso(userId: string, limite: number): Promise<number>;
  salvarTurno(t: Turno): Promise<void>;
}

export class ErroBanco extends Error {}

type Config = { url: string; anonKey: string; serviceKey: string };
type Fetch = typeof fetch;

export function criarRepositorio(cfg: Config, fetchFn: Fetch = fetch): Repositorio {
  const base = cfg.url.replace(/\/$/, "");
  const comoUsuario = (jwt: string) => ({ apikey: cfg.anonKey, Authorization: `Bearer ${jwt}`, Accept: "application/json" });
  const comoServico = { apikey: cfg.serviceKey, Authorization: `Bearer ${cfg.serviceKey}`, "Content-Type": "application/json" };

  async function ler<T>(jwt: string, caminho: string): Promise<T> {
    const r = await fetchFn(`${base}/rest/v1/${caminho}`, { headers: comoUsuario(jwt) });
    if (!r.ok) throw new ErroBanco(`leitura falhou (HTTP ${r.status})`);
    return await r.json() as T;
  }
  async function escrever(caminho: string, metodo: string, corpo: unknown, extra: Record<string, string> = {}) {
    const r = await fetchFn(`${base}/rest/v1/${caminho}`, {
      method: metodo,
      headers: { ...comoServico, Prefer: "return=minimal", ...extra },
      body: corpo === undefined ? undefined : JSON.stringify(corpo),
    });
    if (!r.ok) throw new ErroBanco(`escrita em ${caminho.split("?")[0]} falhou (HTTP ${r.status})`);
  }
  const eq = (v: string) => encodeURIComponent(v);

  return {
    async usuarioDoToken(jwt) {
      const r = await fetchFn(`${base}/auth/v1/user`, { headers: { apikey: cfg.anonKey, Authorization: `Bearer ${jwt}` } });
      if (!r.ok) return null;
      const j = await r.json();
      return typeof j?.id === "string" ? j.id : null;
    },

    async carregarProjeto(jwt, projetoId) {
      const linhas = await ler<Projeto[]>(
        jwt,
        `projetos?id=eq.${eq(projetoId)}&select=id,user_id,titulo,fluxo,ficha,edital_id,edital_titulo&limit=1`,
      );
      return linhas[0] ?? null;
    },

    async carregarSecao(jwt, projetoId, secaoId, dependencias) {
      const p = `projeto_id=eq.${eq(projetoId)}`;
      const [respostas, secoes, mensagens] = await Promise.all([
        ler<ContextoSecao["respostas"]>(jwt, `respostas?${p}&secao_id=eq.${eq(secaoId)}&select=pergunta_id,resposta&order=created_at.asc&limit=100`),
        ler<Array<{ secao_id: string; rascunho: string; aprovado: boolean }>>(
          jwt,
          `projeto_secoes?${p}&select=secao_id,rascunho,aprovado`,
        ),
        ler<Array<{ papel: "usuario" | "assistente"; conteudo: Record<string, unknown> }>>(
          jwt,
          `mensagens?${p}&secao_id=eq.${eq(secaoId)}&select=papel,conteudo&order=created_at.desc&limit=8`,
        ),
      ]);
      return {
        respostas,
        aprovadas: secoes.filter((s) => s.aprovado && dependencias.includes(s.secao_id) && s.rascunho).map((s) => ({ secao_id: s.secao_id, rascunho: s.rascunho })),
        secao: secoes.find((s) => s.secao_id === secaoId) ?? null,
        historico: mensagens.reverse(),
      };
    },

    async carregarCriterios(jwt, editalId) {
      if (editalId == null) return null;
      const colunas = "criterios,teto_orcamento,teto_por_proponente,percentuais_max,percentuais_min,rubricas_vedadas,limites_caracteres,campos_exigidos,contrapartida_exigida,prazo_execucao_meses,exige_acessibilidade,observacoes,revisado,fonte_url,linguagens";
      const l = await ler<NonNullable<Criterios>[]>(jwt, `edital_criterios?edital_id=eq.${editalId}&select=${colunas}&limit=1`);
      return l[0] ?? null;
    },

    async carregarTabelas(jwt, projetoId) {
      const p = `projeto_id=eq.${eq(projetoId)}&order=ordem.asc&select=*&limit=400`;
      const [orcamento_itens, cronograma_itens, midia_itens, equipe_membros] = await Promise.all(
        TABELAS_ESTRUTURADAS.map((t) => ler<Array<Record<string, unknown>>>(jwt, `${t}?${p}`)),
      );
      return { orcamento_itens, cronograma_itens, midia_itens, equipe_membros };
    },

    async carregarTextosDoUsuario(jwt, projetoId) {
      const l = await ler<Array<{ resposta: string }>>(jwt, `respostas?projeto_id=eq.${eq(projetoId)}&select=resposta&order=created_at.asc&limit=500`);
      return l.map((x) => x.resposta);
    },

    async idsDeOutroProjeto(tabela, projetoId, ids) {
      if (!ids.length) return [];
      const r = await fetchFn(`${base}/rest/v1/${tabela}?id=in.(${ids.map(eq).join(",")})&select=id,projeto_id`, { headers: comoServico });
      if (!r.ok) throw new ErroBanco(`leitura de ${tabela} falhou (HTTP ${r.status})`);
      return ((await r.json()) as Array<{ id: string; projeto_id: string }>).filter((x) => x.projeto_id !== projetoId).map((x) => x.id);
    },

    async idsDoOrcamento(projetoId) {
      const r = await fetchFn(`${base}/rest/v1/orcamento_itens?projeto_id=eq.${eq(projetoId)}&select=id&limit=400`, { headers: comoServico });
      if (!r.ok) throw new ErroBanco(`leitura de orcamento_itens falhou (HTTP ${r.status})`);
      return ((await r.json()) as Array<{ id: string }>).map((x) => x.id);
    },

    async substituirItens(tabela, projetoId, linhas) {
      if (linhas.length) {
        await escrever(`${tabela}?on_conflict=id`, "POST", linhas.map((l) => ({ ...l, projeto_id: projetoId })), {
          Prefer: "resolution=merge-duplicates,return=minimal",
        });
      }
      const mantidos = linhas.map((l) => eq(String(l.id))).join(",");
      await escrever(`${tabela}?projeto_id=eq.${eq(projetoId)}${mantidos ? `&id=not.in.(${mantidos})` : ""}`, "DELETE", undefined);
    },

    async limparReferenciasAoOrcamento(projetoId, idsValidos) {
      const r = await fetchFn(`${base}/rest/v1/equipe_membros?projeto_id=eq.${eq(projetoId)}&select=id,orcamento_item_ids`, { headers: comoServico });
      if (!r.ok) throw new ErroBanco(`leitura de equipe_membros falhou (HTTP ${r.status})`);
      const validos = new Set(idsValidos);
      for (const m of (await r.json()) as Array<{ id: string; orcamento_item_ids: string[] }>) {
        const limpos = (m.orcamento_item_ids ?? []).filter((id) => validos.has(id));
        if (limpos.length !== (m.orcamento_item_ids ?? []).length) {
          await escrever(`equipe_membros?id=eq.${eq(m.id)}`, "PATCH", { orcamento_item_ids: limpos });
        }
      }
    },

    async consumirUso(userId, limite) {
      const r = await fetchFn(`${base}/rest/v1/rpc/assistente_consumir_uso`, {
        method: "POST",
        headers: comoServico,
        body: JSON.stringify({ p_user: userId, p_limite: limite }),
      });
      if (!r.ok) throw new ErroBanco(`contador de uso falhou (HTTP ${r.status})`);
      return Number(await r.json());
    },

    async salvarTurno(t) {
      const msgs: Array<Record<string, unknown>> = [];
      if (t.mensagemUsuario) {
        msgs.push({ projeto_id: t.projetoId, secao_id: t.secaoId, papel: "usuario", conteudo: { texto: t.mensagemUsuario } });
      }
      msgs.push({ projeto_id: t.projetoId, secao_id: t.secaoId, papel: "assistente", conteudo: t.resposta });
      await escrever("mensagens", "POST", msgs);
      if (t.respostas.length) {
        await escrever("respostas", "POST", t.respostas.map((r) => ({ projeto_id: t.projetoId, secao_id: t.secaoId, ...r })));
      }
      if (t.resposta.tipo === "rascunho") {
        await escrever("projeto_secoes?on_conflict=projeto_id,secao_id", "POST",
          { projeto_id: t.projetoId, secao_id: t.secaoId, rascunho: t.resposta.rascunho },
          { Prefer: "resolution=merge-duplicates,return=minimal" });
      } else if (t.reabrir) {
        await escrever(`projeto_secoes?projeto_id=eq.${eq(t.projetoId)}&secao_id=eq.${eq(t.secaoId)}`, "PATCH", { aprovado: false });
      }
      await escrever(`projetos?id=eq.${eq(t.projetoId)}&user_id=eq.${eq(t.userId)}`, "PATCH", { ficha: t.ficha });
    },
  };
}
