// Edge Function `registrar-visita` (Deno). Conta uma visita anônima.
// LGPD: o IP é lido do cabeçalho só para (1) o rate limit em memória e (2) uma consulta de
// geolocalização aproximada; nunca é gravado, logado nem devolvido. No banco entram apenas
// contagens agregadas por dia × página × país × UF × cidade (ver a migration de visitas).
import {
  DESCONHECIDA, criarLimitador, ehBot, extrairIp, ipPublico, lerPagina, normalizarGeo,
  origemPermitida, semLocalizacao,
} from "./logica.mjs";

const ORIGENS = (Deno.env.get("ORIGENS_PERMITIDAS") ?? "https://jkfa.github.io")
  .split(",")
  .map((o) => o.trim())
  .filter(Boolean);
const URL_GEO = "https://ipwho.is"; // sem chave, HTTPS, uso comercial permitido (1.000 req/dia por IP de origem)
const TEMPO_GEO_MS = 2000;
const permitir = criarLimitador({ maximo: 3, janelaMs: 60_000 });

function cabecalhosCors(req: Request): Record<string, string> {
  const origem = origemPermitida(req.headers.get("origin"), ORIGENS);
  return origem ? { "Access-Control-Allow-Origin": origem, Vary: "Origin" } : { Vary: "Origin" };
}

async function geolocalizar(ip: string) {
  const controle = new AbortController();
  const temporizador = setTimeout(() => controle.abort(), TEMPO_GEO_MS);
  try {
    const resposta = await fetch(
      `${URL_GEO}/${encodeURIComponent(ip)}?fields=success,country_code,region_code,city`,
      { signal: controle.signal },
    );
    if (!resposta.ok) return DESCONHECIDA; // inclui 429: cota estourada conta como desconhecida
    return normalizarGeo(await resposta.json());
  } catch {
    return DESCONHECIDA;
  } finally {
    clearTimeout(temporizador);
  }
}

async function gravar(pagina: string, geo: { pais: string; uf: string; cidade: string }) {
  const resposta = await fetch(`${Deno.env.get("SUPABASE_URL")}/rest/v1/rpc/registrar_visita_agregada`, {
    method: "POST",
    headers: {
      apikey: Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
      Authorization: `Bearer ${Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ p_pagina: pagina, p_pais: geo.pais, p_uf: geo.uf, p_cidade: geo.cidade }),
  });
  return resposta.ok;
}

Deno.serve(async (req) => {
  const cors = cabecalhosCors(req);
  if (req.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: { ...cors, "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "content-type" },
    });
  }
  if (req.method !== "POST") return new Response(null, { status: 405, headers: cors });
  // Bot: responde 204 como se tivesse contado, sem registrar nada.
  if (ehBot(req.headers.get("user-agent"))) return new Response(null, { status: 204, headers: cors });

  const ip = extrairIp(req.headers);
  if (!permitir(ip ?? "sem-ip")) return new Response(null, { status: 429, headers: cors });

  const pagina = lerPagina(await req.text());
  if (!pagina) return new Response(null, { status: 400, headers: cors });

  const geo =
    !semLocalizacao(req.headers) && ip && ipPublico(ip) ? await geolocalizar(ip) : DESCONHECIDA;
  try {
    const ok = await gravar(pagina, geo);
    if (!ok) console.error("registrar-visita: falha ao gravar agregado");
  } catch {
    console.error("registrar-visita: erro de rede ao gravar agregado"); // nunca loga a requisição
  }
  return new Response(null, { status: 204, headers: cors });
});
