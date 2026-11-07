// Login por link mágico via REST do GoTrue (Supabase Auth). Sem supabase-js.
// A sessão fica em localStorage (por navegador). O portal em si continua sem login.

const CHAVE = "editais-culturais:sessao";

function lerStorage() {
  try {
    return JSON.parse(localStorage.getItem(CHAVE) ?? "null");
  } catch {
    return null;
  }
}
function gravarStorage(sessao) {
  try {
    if (sessao) localStorage.setItem(CHAVE, JSON.stringify(sessao));
    else localStorage.removeItem(CHAVE);
  } catch {
    /* modo privado: a sessão vale só até recarregar */
  }
}

let emMemoria = null;
export const lerSessao = () => emMemoria ?? lerStorage();
export function guardarSessao(sessao) {
  emMemoria = sessao;
  gravarStorage(sessao);
}

/** Dados do payload do JWT (só para exibir; a validação de verdade é do servidor). */
export function dadosDoToken(jwt) {
  try {
    const b64 = jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = decodeURIComponent(
      atob(b64).split("").map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0")).join(""),
    );
    return JSON.parse(json);
  } catch {
    return {};
  }
}

const cabecalhos = (config, extra = {}) => ({ apikey: config.supabaseAnonKey, "Content-Type": "application/json", ...extra });
const base = (config) => `${config.supabaseUrl.replace(/\/$/, "")}/auth/v1`;

/** Lê o retorno do link mágico (#access_token=...) e limpa a URL. Devolve {sessao} ou {erro}. */
export function sessaoDoRetorno(hash = location.hash) {
  if (!hash || hash.length < 2) return null;
  const p = new URLSearchParams(hash.slice(1));
  if (p.get("error") || p.get("error_description")) {
    const codigo = p.get("error_code") ?? "";
    return {
      erro: codigo === "otp_expired"
        ? "Esse link expirou ou já foi usado. Peça um novo abaixo."
        : "Não foi possível entrar com esse link. Peça um novo abaixo.",
    };
  }
  const access = p.get("access_token");
  const refresh = p.get("refresh_token");
  if (!access || !refresh) return null;
  const exp = dadosDoToken(access).exp ?? Math.floor(Date.now() / 1000) + Number(p.get("expires_in") ?? 3600);
  return { sessao: { access_token: access, refresh_token: refresh, expires_at: exp } };
}

export function limparHash() {
  history.replaceState(null, "", location.pathname + location.search);
}

export async function enviarLink(config, email, redirectTo) {
  const r = await fetch(`${base(config)}/otp?redirect_to=${encodeURIComponent(redirectTo)}`, {
    method: "POST",
    headers: cabecalhos(config),
    body: JSON.stringify({ email, create_user: true }),
  });
  if (r.ok) return;
  const j = await r.json().catch(() => ({}));
  if (r.status === 429 || j.error_code === "over_email_send_rate_limit") {
    throw new Error("Muitos pedidos de link em pouco tempo. Espere alguns minutos e tente de novo.");
  }
  if (r.status === 400 || r.status === 422) throw new Error("Confira o e-mail digitado.");
  throw new Error("Não consegui enviar o link agora. Tente de novo em instantes.");
}

export async function renovar(config, sessao) {
  const r = await fetch(`${base(config)}/token?grant_type=refresh_token`, {
    method: "POST",
    headers: cabecalhos(config),
    body: JSON.stringify({ refresh_token: sessao.refresh_token }),
  });
  if (!r.ok) return null;
  const j = await r.json();
  const nova = {
    access_token: j.access_token,
    refresh_token: j.refresh_token,
    expires_at: dadosDoToken(j.access_token).exp ?? Math.floor(Date.now() / 1000) + (j.expires_in ?? 3600),
  };
  guardarSessao(nova);
  return nova;
}

/** Sessão utilizável (renova se faltar menos de 60 s), ou null. */
export async function sessaoAtiva(config, forcar = false) {
  const s = lerSessao();
  if (!s) return null;
  if (!forcar && s.expires_at - 60 > Date.now() / 1000) return s;
  const nova = await renovar(config, s).catch(() => null);
  if (!nova) guardarSessao(null);
  return nova;
}

export async function sair(config) {
  const s = lerSessao();
  guardarSessao(null);
  if (s) {
    await fetch(`${base(config)}/logout`, {
      method: "POST",
      headers: cabecalhos(config, { Authorization: `Bearer ${s.access_token}` }),
    }).catch(() => {});
  }
}
