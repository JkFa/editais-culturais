// Interface única de geração. Trocar de provedor ou modelo = mudar variáveis de ambiente:
//   LLM_PROVIDER=gemini (padrão) | groq (stub)   LLM_MODEL=<id do modelo>
// Segredos (só via `supabase secrets set`): GEMINI_API_KEY, GROQ_API_KEY.
// Nenhum outro arquivo da função conhece o provedor.

export type OpcoesLlm = {
  sistema: string;
  /** Subconjunto portátil de JSON Schema. Se presente, o provedor devolve JSON estruturado. */
  schema?: Record<string, unknown>;
  temperatura?: number;
  maxTokens?: number;
  timeoutMs?: number;
};
export type SaidaLlm = { texto: string; modelo: string };
export type TipoErroLlm = "config" | "cota" | "indisponivel" | "timeout" | "bloqueado" | "requisicao";

export class ErroLlm extends Error {
  constructor(public tipo: TipoErroLlm, mensagem: string) {
    super(mensagem);
  }
}

export interface ProvedorLlm {
  gerar(prompt: string, opcoes: OpcoesLlm): Promise<SaidaLlm>;
}

type Env = { get(chave: string): string | undefined };
type Fetch = typeof fetch;

export const MODELO_PADRAO_GEMINI = "gemini-3.6-flash"; // Flash estável (conferido em 07/11/2026)

/** Gemini usa tipos em maiúsculas no responseSchema. */
export function esquemaParaGemini(s: unknown): unknown {
  if (Array.isArray(s)) return s.map(esquemaParaGemini);
  if (s && typeof s === "object") {
    return Object.fromEntries(
      Object.entries(s).map(([k, v]) => [k, k === "type" && typeof v === "string" ? v.toUpperCase() : esquemaParaGemini(v)]),
    );
  }
  return s;
}

export class ProvedorGemini implements ProvedorLlm {
  constructor(private chave: string, private modelo: string, private fetchFn: Fetch = fetch) {}

  async gerar(prompt: string, o: OpcoesLlm): Promise<SaidaLlm> {
    const controle = new AbortController();
    const t = setTimeout(() => controle.abort(), o.timeoutMs ?? 40_000);
    try {
      const corpo = {
        systemInstruction: { parts: [{ text: o.sistema }] },
        contents: [{ role: "user", parts: [{ text: prompt }] }],
        generationConfig: {
          temperature: o.temperatura ?? 0.4,
          maxOutputTokens: o.maxTokens ?? 4096,
          ...(o.schema ? { responseMimeType: "application/json", responseSchema: esquemaParaGemini(o.schema) } : {}),
        },
      };
      const r = await this.fetchFn(
        `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(this.modelo)}:generateContent`,
        {
          method: "POST",
          headers: { "content-type": "application/json", "x-goog-api-key": this.chave },
          body: JSON.stringify(corpo),
          signal: controle.signal,
        },
      );
      if (r.status === 429) throw new ErroLlm("cota", "cota do provedor esgotada");
      if (r.status >= 500) throw new ErroLlm("indisponivel", `provedor indisponível (HTTP ${r.status})`);
      if (!r.ok) throw new ErroLlm("requisicao", `provedor recusou a requisição (HTTP ${r.status})`);
      const j = await r.json();
      if (j?.promptFeedback?.blockReason) throw new ErroLlm("bloqueado", "conteúdo bloqueado pelo provedor");
      const texto = (j?.candidates?.[0]?.content?.parts ?? []).map((p: { text?: string }) => p.text ?? "").join("");
      if (!texto) throw new ErroLlm("indisponivel", "resposta vazia do provedor");
      return { texto, modelo: this.modelo };
    } catch (e) {
      if (e instanceof ErroLlm) throw e;
      if ((e as Error)?.name === "AbortError") throw new ErroLlm("timeout", "o provedor demorou demais");
      throw new ErroLlm("indisponivel", "falha de rede ao falar com o provedor");
    } finally {
      clearTimeout(t);
    }
  }
}

/**
 * STUB: Groq (API compatível com OpenAI). Para implementar: POST
 * https://api.groq.com/openai/v1/chat/completions, `Authorization: Bearer $GROQ_API_KEY`,
 * `messages: [{role:"system",content:sistema},{role:"user",content:prompt}]`,
 * `response_format: {type:"json_object"}`; ler `choices[0].message.content`. Mapear 429 → "cota".
 */
export class ProvedorGroq implements ProvedorLlm {
  constructor(_chave: string, _modelo: string) {}
  gerar(): Promise<SaidaLlm> {
    return Promise.reject(new ErroLlm("config", "Provedor 'groq' ainda não implementado (ver docs/assistente.md)"));
  }
}

export function criarProvedor(env: Env, fetchFn: Fetch = fetch): ProvedorLlm {
  const provedor = (env.get("LLM_PROVIDER") ?? "gemini").toLowerCase();
  if (provedor === "gemini") {
    const chave = env.get("GEMINI_API_KEY");
    if (!chave) throw new ErroLlm("config", "GEMINI_API_KEY não configurada");
    return new ProvedorGemini(chave, env.get("LLM_MODEL") || MODELO_PADRAO_GEMINI, fetchFn);
  }
  if (provedor === "groq") return new ProvedorGroq(env.get("GROQ_API_KEY") ?? "", env.get("LLM_MODEL") ?? "");
  throw new ErroLlm("config", `LLM_PROVIDER desconhecido: ${provedor}`);
}

/** Ponto de entrada do restante da função: `gerar(prompt, opcoes)`. */
export function gerar(prompt: string, opcoes: OpcoesLlm, provedor?: ProvedorLlm): Promise<SaidaLlm> {
  return (provedor ?? criarProvedor(Deno.env)).gerar(prompt, opcoes);
}
