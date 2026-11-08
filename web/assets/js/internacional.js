// Oportunidades internacionais (Fase 1): rótulos, selos, prazo com fuso, linha em português
// montada SEM LLM a partir dos campos, e a regra de "Posso me inscrever?" para o filtro Internacional.
// Funções puras (testáveis) primeiro; construtores de DOM no fim.
import { el, plural } from "./formatos.js";

export const AMBITOS = [
  { id: "brasil", rotulo: "Brasil" },
  { id: "internacional", rotulo: "Internacional" },
  { id: "todos", rotulo: "Todos" },
];

export const AVISO_LEGAL =
  "Confira sempre a chamada original: requisitos de visto, idioma e documentação são de responsabilidade de quem se candidata.";

export const ROTULOS_ELEGIBILIDADE = {
  aberto: "Aberto a brasileiros",
  requer_residencia: "Requer residência",
  requer_parceiro: "Requer parceiro",
  verificar: "Verificar elegibilidade",
};

export const EXPLICACAO_ELEGIBILIDADE = {
  aberto: "A fonte diz que brasileiros (ou todas as nacionalidades) podem se candidatar.",
  requer_residencia: "Só para quem mora no país de destino.",
  requer_parceiro: "Precisa de uma organização parceira no país/região do edital.",
  verificar: "A fonte não deixa claro se brasileiros podem se candidatar: confira a chamada original.",
};

export const ROTULOS_TIPO_OPORTUNIDADE = {
  residencia: "Residência",
  mobilidade: "Mobilidade",
  projeto: "Projeto",
  premio: "Prêmio",
  formacao: "Formação",
  apresentacao: "Apresentação",
  outro: "Outro tipo",
};

export const ROTULOS_CUSTO = {
  viagem: "viagem",
  hospedagem: "hospedagem",
  cache: "cachê",
  producao: "produção",
  alimentacao: "alimentação",
  acessibilidade: "acessibilidade",
};

const IDIOMAS = { pt: "português", es: "espanhol", en: "inglês", fr: "francês" };
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const CIDADES = {
  "Europe/Brussels": "Bruxelas",
  "Europe/Berlin": "Berlim",
  "Europe/Paris": "Paris",
  "Europe/Madrid": "Madri",
  "Europe/Lisbon": "Lisboa",
  "Europe/London": "Londres",
  "Europe/Rome": "Roma",
  "Europe/Amsterdam": "Amsterdã",
  "America/New_York": "Nova York",
  "America/Bogota": "Bogotá",
  "America/Mexico_City": "Cidade do México",
  "America/Argentina/Buenos_Aires": "Buenos Aires",
  "America/Santiago": "Santiago",
  "America/Sao_Paulo": "Brasília",
  UTC: "UTC",
};

/** O coletor usa "X (ver chamada original)" quando a fonte não diz quem lança a chamada: não repetimos isso. */
export const orgaoVisivel = (e) => (/\(ver chamada original\)$/.test(e.orgao_fomento ?? "") ? null : e.orgao_fomento);

export const ehInternacional = (e) => Boolean(e?.ambito) && e.ambito !== "brasil";

// ---------------------------------------------------------------- países

let nomesDePaises = null;
function nomeDeRegiao(codigo) {
  try {
    nomesDePaises ??= new Intl.DisplayNames(["pt-BR"], { type: "region" });
    return nomesDePaises.of(codigo) ?? codigo;
  } catch {
    return codigo;
  }
}

/** "BE,HR,DE" -> ["BE","HR","DE"]; texto livre ("Ibero-América", "Online") fica como está. */
export function codigosDeDestino(paisDestino) {
  return (paisDestino ?? "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

export function nomesDoDestino(paisDestino) {
  return codigosDeDestino(paisDestino).map((c) => (/^[A-Z]{2}$/.test(c) ? nomeDeRegiao(c) : c));
}

export function destinoPorExtenso(paisDestino) {
  const nomes = nomesDoDestino(paisDestino);
  if (nomes.length <= 2) return nomes.join(" e ");
  return `${nomes.slice(0, -1).join(", ")} e ${nomes.at(-1)}`;
}

// ---------------------------------------------------------------- texto sem LLM

function listaComE(itens) {
  if (itens.length <= 1) return itens.join("");
  return `${itens.slice(0, -1).join(", ")} e ${itens.at(-1)}`;
}

/** "Residência · Alemanha · cobre viagem e hospedagem · em inglês". Só o que os campos dizem. */
export function linhaEmPortugues(e) {
  const partes = [];
  if (e.tipo_oportunidade) partes.push(ROTULOS_TIPO_OPORTUNIDADE[e.tipo_oportunidade] ?? e.tipo_oportunidade);
  if (e.pais_destino) partes.push(destinoPorExtenso(e.pais_destino));
  if (e.custos_cobertos?.length) {
    partes.push(`cobre ${listaComE(e.custos_cobertos.map((c) => ROTULOS_CUSTO[c] ?? c))}`);
  }
  if (e.idioma_original) partes.push(`em ${IDIOMAS[e.idioma_original] ?? e.idioma_original}`);
  return partes.join(" · ");
}

function partesNoFuso(iso, fuso) {
  const formato = new Intl.DateTimeFormat("en-CA", {
    timeZone: fuso,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    hourCycle: "h23",
  });
  const mapa = {};
  for (const p of formato.formatToParts(new Date(iso))) mapa[p.type] = Number(p.value);
  return mapa;
}

function nomeDoFuso(fuso) {
  if (!fuso) return "Brasília";
  return CIDADES[fuso] ?? fuso.split("/").at(-1).replaceAll("_", " ");
}

/**
 * Prazo como a fonte informa. Com horário e fuso: "1 dez 2026, 17h (horário de Bruxelas)".
 * Só data: "21 nov 2026 (confira o horário na chamada)". A conversão para Brasília é da Fase 2.
 */
export function prazoDaFonte(e) {
  const iso = e.prazo_original ?? e.inscricao_fim;
  if (!iso) return "Prazo não informado — ver chamada";
  const fuso = e.fim_tem_horario ? (e.prazo_fuso ?? "America/Sao_Paulo") : "America/Sao_Paulo";
  const p = partesNoFuso(iso, fuso);
  const data = `${p.day} ${MESES[p.month - 1]} ${p.year}`;
  if (!e.fim_tem_horario) return `${data} (confira o horário na chamada)`;
  const hora = p.minute === 0 ? `${p.hour}h` : `${p.hour}h${String(p.minute).padStart(2, "0")}`;
  return `${data}, ${hora} (horário de ${nomeDoFuso(fuso)})`;
}

export function textoWhatsappInternacional(e, urlPortal) {
  const linhas = [`*${e.titulo_original ?? e.titulo}*`];
  const pt = linhaEmPortugues(e);
  if (pt) linhas.push(pt);
  linhas.push(`${ROTULOS_ELEGIBILIDADE[e.elegibilidade_br] ?? ROTULOS_ELEGIBILIDADE.verificar}`);
  linhas.push(`Prazo: ${prazoDaFonte(e)}`);
  linhas.push(`Chamada original: ${e.url_origem}`);
  if (urlPortal) linhas.push(`No portal: ${urlPortal}`);
  linhas.push(AVISO_LEGAL);
  return linhas.join("\n");
}

// ---------------------------------------------------------------- "Posso me inscrever?"

export const RESPOSTAS_VAZIAS = { nacionalidade: "", residencia: "", organizacao: "" };

/** `aberto` só vale como "aberto a brasileiros" para quem tem nacionalidade brasileira. */
export function elegibilidadeEfetiva(e, respostas = RESPOSTAS_VAZIAS) {
  const valor = e.elegibilidade_br ?? "verificar";
  return valor === "aberto" && respostas.nacionalidade === "outra" ? "verificar" : valor;
}

/**
 * Regras (só para oportunidades internacionais):
 * - `requer_residencia` só aparece para quem mora no país de destino (sem resposta, aparece);
 * - `requer_parceiro` aparece com a explicação, salvo para quem diz não ter organização;
 * - `verificar` e `aberto` aparecem sempre (o primeiro com aviso).
 */
export function passaNasRespostas(e, respostas = RESPOSTAS_VAZIAS) {
  const valor = e.elegibilidade_br ?? "verificar";
  if (valor === "requer_residencia" && respostas.residencia) {
    return codigosDeDestino(e.pais_destino).includes(respostas.residencia);
  }
  if (valor === "requer_parceiro" && respostas.organizacao === "nao") return false;
  return true;
}

export function temRespostas(respostas) {
  return Boolean(respostas.nacionalidade || respostas.residencia || respostas.organizacao);
}

export function paisesDosEditais(editais) {
  const codigos = new Set();
  for (const e of editais) {
    if (!ehInternacional(e)) continue;
    for (const c of codigosDeDestino(e.pais_destino)) if (/^[A-Z]{2}$/.test(c)) codigos.add(c);
  }
  return [...codigos].sort((a, b) => nomeDeRegiao(a).localeCompare(nomeDeRegiao(b), "pt-BR"));
}

export function nomeDoPais(codigo) {
  return nomeDeRegiao(codigo);
}

// ---------------------------------------------------------------- DOM

const ICONES = {
  viagem: "M2 16l20-8-8 12-2-5-5-1zM12 15l-2 6",
  hospedagem: "M3 18V7M3 14h18v4M21 14v-2a3 3 0 0 0-3-3h-7v5M7 11h.01",
  cache: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v10M9.5 9.5C9.5 8.5 11 8 12 8s2.5.5 2.5 1.7S13 11.5 12 11.5s-2.5.6-2.5 1.8S11 16 12 16s2.5-.6 2.5-1.5",
  producao: "M4 8l8-4 8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8",
  alimentacao: "M7 3v8M5 3v5a2 2 0 0 0 4 0V3M7 11v10M17 3c-2 2-2 6 0 8v10",
  acessibilidade: "M12 5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM5 8l7 1.5L19 8M12 9.5V15M9 21l3-6 3 6",
};

function icone(nome) {
  const span = el("span", { class: "icone-custo" });
  span.insertAdjacentHTML(
    "beforeend",
    `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="${ICONES[nome] ?? ICONES.cache}"/></svg>`,
  );
  return span;
}

export function custosCobertosEl(e) {
  if (!e.custos_cobertos?.length) return null;
  return el(
    "ul",
    { class: "custos", "aria-label": "Custos cobertos segundo a fonte" },
    e.custos_cobertos.map((c) =>
      el(
        "li",
        { title: `Cobre ${ROTULOS_CUSTO[c] ?? c}` },
        icone(c),
        el("span", { class: "sr-only" }, `Cobre ${ROTULOS_CUSTO[c] ?? c}`),
        el("span", { class: "custo-texto", "aria-hidden": "true" }, ROTULOS_CUSTO[c] ?? c),
      ),
    ),
  );
}

/** Selos do card internacional: elegibilidade (destaque), idioma, tipo. */
export function selosInternacionais(e, respostas = RESPOSTAS_VAZIAS) {
  const valor = elegibilidadeEfetiva(e, respostas);
  const rotulo = ROTULOS_ELEGIBILIDADE[valor] ?? ROTULOS_ELEGIBILIDADE.verificar;
  const itens = [
    el(
      "li",
      {
        class: `pilula selo-elegibilidade ${valor === "aberto" ? "destaque" : valor === "verificar" ? "aviso" : ""}`,
        "aria-label": `Elegibilidade: ${rotulo}. ${EXPLICACAO_ELEGIBILIDADE[valor]}`,
        title: EXPLICACAO_ELEGIBILIDADE[valor],
      },
      rotulo,
    ),
  ];
  if (e.idioma_original) {
    itens.push(
      el(
        "li",
        { class: "pilula selo-idioma", "aria-label": `Idioma da chamada: ${IDIOMAS[e.idioma_original] ?? e.idioma_original}` },
        e.idioma_original.toUpperCase(),
      ),
    );
  }
  if (e.tipo_oportunidade) {
    itens.push(
      el(
        "li",
        { class: "pilula selo-tipo", "aria-label": `Tipo: ${ROTULOS_TIPO_OPORTUNIDADE[e.tipo_oportunidade]}` },
        ROTULOS_TIPO_OPORTUNIDADE[e.tipo_oportunidade] ?? e.tipo_oportunidade,
      ),
    );
  }
  return el("ul", { class: "pilulas pilulas-intl", "aria-label": "Elegibilidade, idioma e tipo" }, itens);
}

export function valorOriginalEl(e) {
  if (e.valor_original == null) return null;
  let texto;
  try {
    texto = new Intl.NumberFormat("pt-BR", { style: "currency", currency: e.moeda || "EUR" }).format(e.valor_original);
  } catch {
    texto = `${e.valor_original} ${e.moeda ?? ""}`.trim();
  }
  return el("p", { class: "valor-original" }, `Valor informado: ${texto} (sem conversão para reais)`);
}

/** "Fonte: On the Move" com link para a chamada/página original. */
export function fonteInternacionalEl(e) {
  const nome = e.via || "fonte original";
  return el(
    "p",
    { class: "via via-intl" },
    "Fonte: ",
    el("a", { href: e.url_origem, target: "_blank", rel: "noopener noreferrer" }, nome),
  );
}

export function resumoDeContagem(n) {
  return plural(n, "oportunidade", "oportunidades");
}
