// Formatação em pt-BR e utilitários de DOM. Datas sempre no fuso de Brasília,
// qualquer que seja o fuso do navegador.

export const FUSO = "America/Sao_Paulo";
export const NAO_INFORMADO = "Não informado no resumo — ver edital";

const moeda = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const dataCurta = new Intl.DateTimeFormat("pt-BR", {
  timeZone: FUSO,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});
const dataDiaMes = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, day: "2-digit", month: "2-digit" });
const partes = new Intl.DateTimeFormat("en-CA", {
  timeZone: FUSO,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

export const ROTULOS_PROPONENTE = {
  PF: "Pessoa física",
  MEI: "MEI",
  PJ_COM_FINS: "PJ com fins lucrativos",
  PJ_SEM_FINS: "PJ sem fins (OSC)",
  COLETIVO: "Coletivo sem CNPJ",
};

export function formatarMoeda(valor) {
  return valor == null ? null : moeda.format(valor);
}

const moedaCompacta = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  notation: "compact",
  maximumFractionDigits: 1,
});

/** "R$ 5,2 mi": para números grandes em espaço curto. O valor exato vai no title. */
export function formatarMoedaCompacta(valor) {
  return valor == null ? null : moedaCompacta.format(valor);
}

export function formatarData(iso) {
  return dataCurta.format(new Date(iso));
}

export function formatarDiaMes(iso) {
  return dataDiaMes.format(new Date(iso));
}

/** {ano, mes, dia, hora, minuto} no fuso de Brasília. */
export function partesBrasilia(dataOuIso) {
  const mapa = {};
  for (const p of partes.formatToParts(new Date(dataOuIso))) mapa[p.type] = p.value;
  return {
    ano: Number(mapa.year),
    mes: Number(mapa.month),
    dia: Number(mapa.day),
    hora: Number(mapa.hour),
    minuto: Number(mapa.minute),
  };
}

/** "18h" ou "17h59". */
export function formatarHora(iso) {
  const { hora, minuto } = partesBrasilia(iso);
  return minuto === 0 ? `${hora}h` : `${hora}h${String(minuto).padStart(2, "0")}`;
}

export function formatarDataHora(iso) {
  const { hora, minuto } = partesBrasilia(iso);
  return `${formatarData(iso)} ${String(hora).padStart(2, "0")}:${String(minuto).padStart(2, "0")}`;
}

/** Prazo final por extenso: "30/04/2027" ou "30/04/2027 às 17h59". */
export function prazoPorExtenso(edital) {
  const data = formatarData(edital.inscricao_fim);
  return edital.fim_tem_horario ? `${data} às ${formatarHora(edital.inscricao_fim)}` : data;
}

export function semAcento(texto) {
  return (texto ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
}

export function listaDeProponentes(edital) {
  return (edital.proponentes ?? []).map((p) => ROTULOS_PROPONENTE[p] ?? p);
}

const NOMES_FONTE = {
  curadoria: "Curadoria",
  "minc-inscricoes-abertas": "Ministério da Cultura",
  "cultbr-pnab": "CultBR (MinC)",
  es: "Mapa Cultural do Espírito Santo",
  "cim-amfri": "Mapa Cultural do CIM-AMFRI",
  parauapebas: "Mapa Cultural de Parauapebas",
  br: "Mapa Cultural do Brasil",
  ce: "Mapa Cultural do Ceará",
  pe: "Mapa Cultural de Pernambuco",
  se: "Mapa Cultural de Sergipe",
  pa: "Mapa Cultural do Pará",
  pb: "Mapa Cultural da Paraíba",
  aracaju: "Mapa Cultural de Aracaju",
  natal: "Mapa Cultural de Natal",
  campinas: "Mapa Cultural de Campinas",
};

export function nomeDaFonte(fonte) {
  const slug = fonte.startsWith("mapas:") ? fonte.slice(6) : fonte;
  return NOMES_FONTE[slug] ?? fonte;
}

/** Local do edital para exibir: "Nacional", "Aracaju/SE", "CE". */
export function localDoEdital(edital) {
  if (edital.abrangencia === "nacional") return "Nacional";
  if (edital.municipio && edital.uf) return `${edital.municipio}/${edital.uf}`;
  return edital.municipio || edital.uf || null;
}

export function resumoDeValores(edital) {
  if (edital.tipo_apoio === "cessao_de_espaco") return "Cessão de espaço (sem repasse)";
  const linhas = [];
  if (edital.valor_total != null) linhas.push(`Total: ${formatarMoeda(edital.valor_total)}`);
  if (edital.teto_por_projeto != null) {
    linhas.push(`Teto por projeto: ${formatarMoeda(edital.teto_por_projeto)}`);
  }
  return linhas.length ? linhas.join(" · ") : `Valor: ${NAO_INFORMADO}`;
}

/** Texto pronto para colar no WhatsApp (negrito com *asteriscos*). */
export function textoWhatsapp(edital, urlPortal) {
  const quem = listaDeProponentes(edital);
  return [
    `*${edital.titulo}*`,
    edital.orgao_fomento,
    resumoDeValores(edital),
    `Prazo: até ${prazoPorExtenso(edital)}`,
    `Quem pode participar: ${quem.length ? quem.join(", ") : NAO_INFORMADO}`,
    `Mais detalhes: ${urlPortal}`,
  ].join("\n");
}

/** Link para o assistente de projetos, já com o edital escolhido (qualquer página do site). */
export function urlAssistente(edital) {
  const url = new URL("../../projeto/", import.meta.url);
  url.searchParams.set("novo", "1");
  if (edital.chave) url.searchParams.set("edital", edital.chave);
  return url.href;
}

/** Cria elemento sem innerHTML: todo texto entra via textContent. */
export function el(tag, atributos = {}, ...filhos) {
  const no = document.createElement(tag);
  for (const [chave, valor] of Object.entries(atributos)) {
    if (valor == null || valor === false) continue;
    if (chave === "class") no.className = valor;
    else if (chave === "dataset") Object.assign(no.dataset, valor);
    else if (chave.startsWith("on") && typeof valor === "function") {
      no.addEventListener(chave.slice(2), valor);
    } else no.setAttribute(chave, valor === true ? "" : valor);
  }
  for (const filho of filhos.flat()) {
    if (filho == null || filho === false) continue;
    no.append(filho.nodeType ? filho : document.createTextNode(String(filho)));
  }
  return no;
}

export function plural(n, singular, pluralForma) {
  return `${n} ${n === 1 ? singular : pluralForma}`;
}

export const ROTULOS_TIPO_APOIO = {
  fomento_em_dinheiro: "Fomento em dinheiro",
  premio: "Prêmio",
  patrocinio_incentivado: "Patrocínio incentivado",
  cessao_de_espaco: "Cessão de espaço (sem repasse)",
  credenciamento: "Credenciamento",
};

/** "Fonte: Secretaria da Cultura do Ceará · via Mapas Culturais (CE)"; sem plataforma, só a fonte. */
export function textoDaFonte(edital) {
  if (edital.via) return `Fonte: ${edital.orgao_fomento} · via ${edital.via}`;
  return `Fonte: ${nomeDaFonte(edital.fonte)}`;
}
