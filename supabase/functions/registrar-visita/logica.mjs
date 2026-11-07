// Lógica pura da Edge Function `registrar-visita`, separada para ser testável em Node
// (`node --test`) e usada pelo Deno no `index.ts`. Nada aqui guarda ou registra IP em log.

const BOTS =
  /bot|crawl|spider|slurp|headless|lighthouse|pagespeed|gtmetrix|wget|curl|python|node-fetch|axios|go-http|java\/|okhttp|preview|monitor|uptime|facebookexternalhit|whatsapp|telegram|ahrefs|semrush/i;

export const PAGINAS = new Set(["home", "editais", "sobre"]);

export function ehBot(userAgent) {
  return !userAgent || BOTS.test(userAgent);
}

/** Do Not Track ou Global Privacy Control: conta a visita, sem localização. */
export function semLocalizacao(cabecalhos) {
  return cabecalhos.get("dnt") === "1" || cabecalhos.get("sec-gpc") === "1";
}

/** IP só em memória, para geolocalizar e para o rate limit. */
export function extrairIp(cabecalhos) {
  const encaminhado = cabecalhos.get("x-forwarded-for")?.split(",")[0]?.trim();
  return encaminhado || cabecalhos.get("cf-connecting-ip") || cabecalhos.get("x-real-ip") || null;
}

export function ipPublico(ip) {
  if (!ip) return false;
  if (/^(10\.|127\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.)/.test(ip)) return false;
  if (/^(::1?$|fc|fd|fe80)/i.test(ip)) return false;
  return /^[0-9a-f:.]+$/i.test(ip);
}

export function origemPermitida(origem, permitidas) {
  if (!origem) return null;
  return permitidas.includes(origem) ? origem : null;
}

/** No máximo `maximo` registros por IP a cada `janelaMs`. Estado só em memória. */
export function criarLimitador({ maximo = 3, janelaMs = 60_000, agora = () => Date.now() } = {}) {
  const porIp = new Map();
  return function permitir(ip) {
    const t = agora();
    const recentes = (porIp.get(ip) ?? []).filter((x) => t - x < janelaMs);
    if (recentes.length >= maximo) {
      porIp.set(ip, recentes);
      return false;
    }
    recentes.push(t);
    porIp.set(ip, recentes);
    if (porIp.size > 5000) {
      for (const [chave, marcas] of porIp) {
        if (marcas.every((x) => t - x >= janelaMs)) porIp.delete(chave);
      }
    }
    return true;
  };
}

export const DESCONHECIDA = { pais: "ZZ", uf: "ZZ", cidade: "" };

/** Resposta do ipwho.is -> {pais, uf, cidade}. Fora do Brasil guarda só o país. */
export function normalizarGeo(json) {
  if (!json || json.success === false) return DESCONHECIDA;
  const pais = /^[A-Za-z]{2}$/.test(json.country_code ?? "") ? json.country_code.toUpperCase() : "ZZ";
  if (pais !== "BR") return { pais, uf: "ZZ", cidade: "" };
  const uf = /^[A-Za-z]{2}$/.test(json.region_code ?? "") ? json.region_code.toUpperCase() : "ZZ";
  const cidade = String(json.city ?? "").trim().slice(0, 80);
  return { pais, uf, cidade };
}

export function lerPagina(texto) {
  try {
    const corpo = JSON.parse(texto);
    return PAGINAS.has(corpo?.pagina) ? corpo.pagina : null;
  } catch {
    return null;
  }
}
