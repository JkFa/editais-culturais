// Estado dos filtros e funções puras de filtragem, ordenação e contagem.
import { semAcento } from "./formatos.js";
import { ehInternacional, nomeDoPais, nomesDoDestino, passaNasRespostas } from "./internacional.js";
import { diasRestantes, encerrado } from "./prazos.js";

export const LINGUAGENS = [
  "Música",
  "Cinema & Audiovisual",
  "Teatro & Dança",
  "Fotografia",
  "Artes Visuais",
  "Literatura",
  "Patrimônio",
  "Multiáreas",
];

export const PERFIS = [
  { id: "PF", rotulo: "Pessoa física" },
  { id: "MEI", rotulo: "MEI" },
  { id: "PJ_COM_FINS", rotulo: "PJ com fins" },
  { id: "PJ_SEM_FINS", rotulo: "PJ sem fins (OSC)" },
  { id: "COLETIVO", rotulo: "Coletivo sem CNPJ" },
];

export const UFS = "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split(" ");

export const TIPOS_APOIO = [
  { id: "fomento_em_dinheiro", rotulo: "Fomento em dinheiro" },
  { id: "premio", rotulo: "Prêmio" },
  { id: "patrocinio_incentivado", rotulo: "Patrocínio incentivado" },
  { id: "cessao_de_espaco", rotulo: "Cessão de espaço" },
  { id: "credenciamento", rotulo: "Credenciamento" },
];

export const ORDENS = [
  { id: "prazo", rotulo: "Prazo mais próximo" },
  { id: "teto", rotulo: "Maior teto por projeto" },
  { id: "recentes", rotulo: "Recém-publicados" },
];

export function estadoInicial() {
  return {
    q: "",
    perfil: "",
    linguagens: [],
    abrangencia: "", // "" | "nacional"
    ambito: "brasil", // "brasil" | "internacional" | "todos"
    nacionalidade: "", // "" | "brasileira" | "outra" (só internacional)
    residencia: "", // "" | código ISO do país | "OUTRO" (só internacional)
    organizacao: "", // "" | "sim" | "nao" (só internacional)
    uf: "",
    tipoApoio: "",
    apenasAbertas: true,
    ordem: "prazo",
    aba: "todos", // "todos" | "favoritos"
  };
}

export function haystack(edital) {
  return semAcento(
    [
      edital.titulo,
      edital.titulo_original,
      edital.orgao_fomento,
      edital.resumo_simples,
      edital.municipio,
      edital.uf,
      ...(ehInternacional(edital) ? nomesDoDestino(edital.pais_destino) : []),
    ].join(" "),
  );
}

function bateComBusca(edital, termos, chavesServidor) {
  if (!termos.length) return true;
  if (chavesServidor?.has(edital.chave)) return true;
  const texto = haystack(edital);
  return termos.every((t) => texto.includes(t));
}

/**
 * Filtra os editais. `chavesServidor` (opcional) são os acertos da busca textual do
 * Postgres; ele soma aos acertos por substring no cliente (que cobrem prefixos).
 */
export function filtrar(editais, estado, agora = new Date(), chavesServidor = null) {
  const termos = semAcento(estado.q).split(/\s+/).filter(Boolean);
  return editais.filter((e) => {
    if (estado.apenasAbertas && encerrado(e, agora)) return false;
    const fora = ehInternacional(e);
    const ambito = estado.ambito ?? "brasil"; // estados antigos, sem o campo, são do Brasil
    if (ambito === "brasil" && fora) return false;
    if (ambito === "internacional" && !fora) return false;
    if (fora) {
      // Perfil, UF, abrangência e tipo de apoio são do Brasil: não valem para oportunidades de fora.
      if (estado.linguagens.length && !estado.linguagens.some((l) => e.linguagens?.includes(l))) return false;
      if (!passaNasRespostas(e, estado)) return false;
      return bateComBusca(e, termos, chavesServidor);
    }
    // Proponentes vazio = elegibilidade desconhecida: continua visível.
    if (estado.perfil && e.proponentes?.length && !e.proponentes.includes(estado.perfil)) return false;
    if (estado.linguagens.length && !estado.linguagens.some((l) => e.linguagens?.includes(l))) return false;
    if (estado.abrangencia === "nacional" && e.abrangencia !== "nacional") return false;
    // Com UF escolhida aparecem os editais da UF e também os nacionais.
    if (estado.uf && e.uf !== estado.uf && e.abrangencia !== "nacional") return false;
    // Tipo de apoio nulo = não informado no resumo: some sob o filtro (não dá para afirmar nada).
    if (estado.tipoApoio && e.tipo_apoio !== estado.tipoApoio) return false;
    return bateComBusca(e, termos, chavesServidor);
  });
}

export function ordenar(editais, ordem) {
  const porPrazo = (a, b) => new Date(a.inscricao_fim) - new Date(b.inscricao_fim);
  const copia = [...editais];
  if (ordem === "teto") {
    // Nulos no fim: sem teto informado não é teto zero.
    return copia.sort((a, b) => {
      if (a.teto_por_projeto == null && b.teto_por_projeto == null) return porPrazo(a, b);
      if (a.teto_por_projeto == null) return 1;
      if (b.teto_por_projeto == null) return -1;
      return b.teto_por_projeto - a.teto_por_projeto || porPrazo(a, b);
    });
  }
  if (ordem === "recentes") {
    return copia.sort((a, b) => {
      const pa = a.publicado_em ? new Date(a.publicado_em).getTime() : -Infinity;
      const pb = b.publicado_em ? new Date(b.publicado_em).getTime() : -Infinity;
      return pb - pa || porPrazo(a, b);
    });
  }
  return copia.sort(porPrazo);
}

/** Contadores do cabeçalho. O radar nunca entra aqui. */
export function contadores(editais, agora = new Date()) {
  const abertosTodos = editais.filter((e) => !encerrado(e, agora));
  // Os números do Brasil não mudam com as oportunidades de fora; elas têm contador próprio.
  const abertos = abertosTodos.filter((e) => !ehInternacional(e));
  const internacionais = abertosTodos.filter(ehInternacional);
  const comValor = abertos.filter((e) => e.valor_total != null);
  return {
    abertos: abertos.length,
    encerrandoEm7Dias: abertos.filter((e) => diasRestantes(e.inscricao_fim, agora) <= 7).length,
    comValorInformado: comValor.length,
    somaValores: comValor.reduce((soma, e) => soma + e.valor_total, 0),
    internacionaisAbertos: internacionais.length,
    internacionaisEncerrandoEm7Dias: internacionais.filter((e) => diasRestantes(e.inscricao_fim, agora) <= 7).length,
  };
}

export function filtrosAtivos(estado) {
  const ativos = [];
  if (estado.q) ativos.push({ id: "q", rotulo: `busca "${estado.q}"` });
  if (estado.perfil) ativos.push({ id: "perfil", rotulo: PERFIS.find((p) => p.id === estado.perfil)?.rotulo ?? estado.perfil });
  for (const l of estado.linguagens) ativos.push({ id: `ling:${l}`, rotulo: l });
  if (estado.ambito === "internacional") ativos.push({ id: "ambito", rotulo: "Internacional" });
  if (estado.ambito === "todos") ativos.push({ id: "ambito", rotulo: "Brasil e internacional" });
  if (estado.nacionalidade) ativos.push({ id: "nacionalidade", rotulo: estado.nacionalidade === "brasileira" ? "Nacionalidade brasileira" : "Outra nacionalidade" });
  if (estado.residencia) ativos.push({ id: "residencia", rotulo: `Moro em ${estado.residencia === "OUTRO" ? "outro país" : nomeDoPais(estado.residencia)}` });
  if (estado.organizacao) ativos.push({ id: "organizacao", rotulo: estado.organizacao === "sim" ? "Tenho organização" : "Sem organização" });
  if (estado.abrangencia) ativos.push({ id: "abrangencia", rotulo: "Nacional" });
  if (estado.uf) ativos.push({ id: "uf", rotulo: estado.uf });
  if (estado.tipoApoio) {
    ativos.push({ id: "tipoApoio", rotulo: TIPOS_APOIO.find((t) => t.id === estado.tipoApoio)?.rotulo ?? estado.tipoApoio });
  }
  return ativos;
}
