// Edge Function `assistente` (Deno). Autenticada pelo JWT do usuário (Supabase Auth).
// Deploy: supabase functions deploy assistente   (ver docs/assistente.md)
import { criarHandler } from "./handler.ts";
import { criarProvedor } from "./llm.ts";
import { criarRepositorio } from "./repositorio.ts";
import specs from "./specs.gen.json" with { type: "json" };

const env = Deno.env;
const origens = (env.get("ORIGENS_PERMITIDAS") ?? "https://jkfa.github.io").split(",").map((o) => o.trim()).filter(Boolean);

Deno.serve(
  criarHandler({
    repo: criarRepositorio({
      url: env.get("SUPABASE_URL")!,
      anonKey: env.get("SUPABASE_ANON_KEY")!,
      serviceKey: env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    }),
    provedor: () => criarProvedor(env),
    specs,
    origens,
    limiteDiario: Number(env.get("LIMITE_DIARIO") ?? 60) || 60,
    log: (e) => console.log(JSON.stringify(e)),
  }),
);
