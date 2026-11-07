// Carregamento de dados: snapshot.json primeiro (rápido, cacheável), Supabase ao vivo em seguida.
// O snapshot é commitado todo dia e sempre tem dados reais; assim a página aparece na hora
// e o site continua no ar se o Supabase pausar ou estourar a cota.
// Fala com o PostgREST do Supabase por fetch puro: dispensa supabase-js (~100 KB, via CDN).

const TIMEOUT_MS = 4000;
const URL_SNAPSHOT = new URL("../../data/snapshot.json", import.meta.url);
const COLUNAS = [
  "fonte", "id_externo", "chave", "url_origem", "url_inscricao", "url_edital_pdf", "titulo",
  "orgao_fomento", "resumo_original", "resumo_simples", "linguagens", "abrangencia", "uf",
  "municipio", "proponentes", "valor_total", "teto_por_projeto", "num_cotas",
  "exige_contrapartida", "marco_legal", "documentos_exigidos", "cronograma", "tipo_apoio",
  "verificado_em", "pendencias", "inscricao_inicio", "inscricao_fim", "fim_tem_horario",
  "publicado_em", "coletado_em", "atualizado_em", "proveniencia", "via", "fontes_alternativas",
  "requisitos",
].join(",");

export function configurado(config) {
  return Boolean(config?.supabaseUrl && config?.supabaseAnonKey);
}

/** Chaves novas (`sb_publishable_...`) não são JWT: vão só em `apikey`. */
export function cabecalhosApi(config) {
  const cabecalhos = { apikey: config.supabaseAnonKey, Accept: "application/json" };
  if (!config.supabaseAnonKey.startsWith("sb_")) {
    cabecalhos.Authorization = `Bearer ${config.supabaseAnonKey}`;
  }
  return cabecalhos;
}

async function buscarJson(config, caminho, opcoes = {}, ms = TIMEOUT_MS) {
  const controle = new AbortController();
  const temporizador = setTimeout(() => controle.abort(), ms);
  try {
    const resposta = await fetch(`${config.supabaseUrl.replace(/\/$/, "")}/rest/v1/${caminho}`, {
      ...opcoes,
      headers: { ...cabecalhosApi(config), ...opcoes.headers },
      signal: controle.signal,
    });
    if (!resposta.ok) throw new Error(`Supabase: HTTP ${resposta.status}`);
    return await resposta.json();
  } catch (erro) {
    throw erro.name === "AbortError" ? new Error(`Supabase: tempo esgotado (${ms} ms)`) : erro;
  } finally {
    clearTimeout(temporizador);
  }
}

async function carregarAoVivo(config) {
  const [editais, radar] = await Promise.all([
    buscarJson(config, `editais_abertos?select=${COLUNAS}&order=inscricao_fim.asc&limit=1000`),
    buscarJson(config, "radar_editais?select=*"),
  ]);
  return { editais, radar };
}

/** Busca textual do Postgres (português, websearch). Devolve o conjunto de chaves. */
async function buscarNoServidor(config, termo) {
  const filtro = `wfts(portuguese).${termo}`;
  const linhas = await buscarJson(
    config,
    `editais_abertos?select=chave&busca_textual=${encodeURIComponent(filtro)}`,
  );
  return new Set(linhas.map((linha) => linha.chave));
}

/** Resumo agregado de visitas (função `visitas_resumo`). `null` se indisponível. */
export async function carregarVisitas(config) {
  if (!configurado(config)) return null;
  try {
    return await buscarJson(
      config,
      "rpc/visitas_resumo",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    );
  } catch {
    return null;
  }
}

async function carregarSnapshot() {
  const resposta = await fetch(URL_SNAPSHOT);
  if (!resposta.ok) throw new Error(`snapshot.json: HTTP ${resposta.status}`);
  return resposta.json();
}

/**
 * Chama `aoDados` primeiro com o snapshot e depois, se der certo, com os dados ao vivo.
 * Rejeita só se as duas origens falharem.
 *
 * `aoDados` recebe {editais, radar, origem: "snapshot"|"ao-vivo", geradoEm, fontesOk,
 * atualizando, motivoFallback, buscarNoServidor}.
 */
export async function carregarProgressivo(config, aoDados) {
  const vivo = configurado(config)
    ? carregarAoVivo(config).then(
        (dados) => ({ dados }),
        (erro) => ({ erro }),
      )
    : null;
  let snapshot = null;
  let erroSnapshot = null;
  try {
    snapshot = await carregarSnapshot();
  } catch (erro) {
    erroSnapshot = erro;
  }
  const base = {
    geradoEm: snapshot?.gerado_em ?? null,
    fontesOk: snapshot?.fontes_ok ?? [],
    fontes: snapshot?.fontes ?? [],
    motivoFallback: null,
    atualizando: false,
    buscarNoServidor: null,
  };
  if (snapshot) {
    aoDados({
      ...base,
      editais: snapshot.editais,
      radar: snapshot.radar ?? [],
      origem: "snapshot",
      atualizando: Boolean(vivo),
    });
  }
  if (!vivo) {
    if (!snapshot) throw erroSnapshot;
    return;
  }
  const resultado = await vivo;
  if (resultado.dados) {
    aoDados({
      ...base,
      editais: resultado.dados.editais,
      radar: resultado.dados.radar,
      origem: "ao-vivo",
      buscarNoServidor: (termo) => buscarNoServidor(config, termo),
    });
    return;
  }
  console.warn("Supabase indisponível, ficando com o snapshot:", resultado.erro.message);
  if (!snapshot) throw erroSnapshot;
  aoDados({
    ...base,
    editais: snapshot.editais,
    radar: snapshot.radar ?? [],
    origem: "snapshot",
    motivoFallback: resultado.erro.message,
  });
}
