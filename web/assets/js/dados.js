// Carregamento de dados: Supabase (ao vivo) -> snapshot.json (cópia offline).
// O snapshot existe para o site nunca ficar fora do ar se o Supabase pausar ou
// estourar a cota; ele é commitado diariamente e sempre contém dados reais.

const TIMEOUT_MS = 4000;
const SUPABASE_JS = "https://esm.sh/@supabase/supabase-js@2.45.4";
const COLUNAS = [
  "fonte", "id_externo", "chave", "url_origem", "url_inscricao", "url_edital_pdf", "titulo",
  "orgao_fomento", "resumo_original", "resumo_simples", "linguagens", "abrangencia", "uf",
  "municipio", "proponentes", "valor_total", "teto_por_projeto", "num_cotas",
  "exige_contrapartida", "marco_legal", "documentos_exigidos", "cronograma", "tipo_apoio",
  "verificado_em", "pendencias", "inscricao_inicio", "inscricao_fim", "fim_tem_horario",
  "publicado_em", "coletado_em", "atualizado_em", "proveniencia",
].join(",");

function comTimeout(promessa, ms, rotulo) {
  let temporizador;
  const limite = new Promise((_, rejeitar) => {
    temporizador = setTimeout(() => rejeitar(new Error(`${rotulo}: tempo esgotado (${ms} ms)`)), ms);
  });
  return Promise.race([promessa, limite]).finally(() => clearTimeout(temporizador));
}

function configurado(config) {
  return Boolean(config?.supabaseUrl && config?.supabaseAnonKey);
}

async function carregarSupabase(config) {
  const { createClient } = await import(SUPABASE_JS);
  const cliente = createClient(config.supabaseUrl, config.supabaseAnonKey, {
    auth: { persistSession: false },
  });
  const [editais, radar] = await Promise.all([
    cliente.from("editais_abertos").select(COLUNAS).order("inscricao_fim", { ascending: true }),
    cliente.from("radar_editais").select("*"),
  ]);
  if (editais.error) throw new Error(editais.error.message);
  if (radar.error) throw new Error(radar.error.message);
  return { cliente, editais: editais.data, radar: radar.data };
}

async function carregarSnapshot() {
  const resposta = await fetch("data/snapshot.json", { cache: "no-cache" });
  if (!resposta.ok) throw new Error(`snapshot.json: HTTP ${resposta.status}`);
  return resposta.json();
}

/**
 * @returns {Promise<{editais, radar, origem: "ao-vivo"|"snapshot", geradoEm: string|null,
 *   motivoFallback: string|null, buscarNoServidor: ((q: string) => Promise<Set<string>>)|null}>}
 */
export async function carregarDados(config) {
  let motivoFallback = null;
  if (configurado(config)) {
    try {
      const vivo = await comTimeout(carregarSupabase(config), TIMEOUT_MS, "Supabase");
      return {
        editais: vivo.editais,
        radar: vivo.radar,
        origem: "ao-vivo",
        geradoEm: null,
        motivoFallback: null,
        buscarNoServidor: (q) => buscarNoServidor(vivo.cliente, q),
      };
    } catch (erro) {
      motivoFallback = erro.message;
      console.warn("Supabase indisponível, usando o snapshot:", erro.message);
    }
  }
  const snapshot = await carregarSnapshot();
  return {
    editais: snapshot.editais,
    radar: snapshot.radar ?? [],
    origem: "snapshot",
    geradoEm: snapshot.gerado_em,
    motivoFallback,
    buscarNoServidor: null,
  };
}

/** Busca textual do Postgres (português, websearch). Devolve o conjunto de chaves. */
async function buscarNoServidor(cliente, termo) {
  const { data, error } = await comTimeout(
    cliente
      .from("editais_abertos")
      .select("chave")
      .textSearch("busca_textual", termo, { config: "portuguese", type: "websearch" }),
    TIMEOUT_MS,
    "busca",
  );
  if (error) throw new Error(error.message);
  return new Set(data.map((linha) => linha.chave));
}
