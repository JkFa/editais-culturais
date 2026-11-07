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
      body: JSON.stringify(corpo),
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
      const l = await ler<NonNullable<Criterios>[]>(jwt, `edital_criterios?edital_id=eq.${editalId}&select=criterios,teto_orcamento,linguagens,campos_exigidos,limites_caracteres&limit=1`);
      return l[0] ?? null;
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
