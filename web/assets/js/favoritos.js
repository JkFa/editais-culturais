// Favoritos em localStorage (com fallback em memória) e exportações.
import {
  NAO_INFORMADO,
  formatarData,
  formatarHora,
  listaDeProponentes,
  partesBrasilia,
  prazoPorExtenso,
  resumoDeValores,
} from "./formatos.js";
import { encerrado } from "./prazos.js";

const CHAVE = "editais-culturais:favoritos:v1";

export class Favoritos {
  constructor() {
    this.memoria = new Map();
    this.persistente = true;
    this.carregar();
  }

  carregar() {
    try {
      const bruto = localStorage.getItem(CHAVE);
      const dados = bruto ? JSON.parse(bruto) : {};
      this.memoria = new Map(Object.entries(dados));
    } catch {
      this.persistente = false;
    }
  }

  gravar() {
    try {
      localStorage.setItem(CHAVE, JSON.stringify(Object.fromEntries(this.memoria)));
      this.persistente = true;
    } catch {
      // `persistente` falso é lido pela interface, que avisa a pessoa.
      this.persistente = false;
    }
  }

  tem(chave) {
    return this.memoria.has(chave);
  }

  get total() {
    return this.memoria.size;
  }

  /** Guarda o mínimo para listar o edital mesmo depois de ele sair da base. */
  alternar(edital) {
    if (this.memoria.has(edital.chave)) this.memoria.delete(edital.chave);
    else {
      this.memoria.set(edital.chave, {
        chave: edital.chave,
        titulo: edital.titulo,
        orgao_fomento: edital.orgao_fomento,
        url_origem: edital.url_origem,
        inscricao_fim: edital.inscricao_fim,
        fim_tem_horario: edital.fim_tem_horario,
      });
    }
    this.gravar();
    return this.memoria.has(edital.chave);
  }

  remover(chave) {
    this.memoria.delete(chave);
    this.gravar();
  }

  salvos() {
    return [...this.memoria.values()];
  }
}

/** Junta o que está na base com o que só existe salvo. */
export function resolverFavoritos(favoritos, editais) {
  const porChave = new Map(editais.map((e) => [e.chave, e]));
  return favoritos.salvos().map((salvo) => ({ salvo, edital: porChave.get(salvo.chave) ?? null }));
}

// ---------------------------------------------------------------- exportações

function celulaCsv(valor) {
  let texto = String(valor ?? "");
  // Evita que o Excel execute fórmulas vindas de títulos públicos.
  if (/^[=+\-@\t\r]/.test(texto)) texto = `'${texto}`;
  return `"${texto.replaceAll('"', '""')}"`;
}

/** CSV UTF-8 com BOM, separador ";" (padrão do Excel em pt-BR) e CRLF. */
export function gerarCsv(itens, urlDoPortal, agora = new Date()) {
  const cabecalho = [
    "Título", "Órgão", "Prazo final", "Horário", "Situação", "Valor total", "Teto por projeto",
    "Quem pode participar", "Link oficial", "Link no portal",
  ];
  const linhas = itens.map(({ salvo, edital }) => {
    if (!edital) {
      return [
        salvo.titulo, salvo.orgao_fomento, formatarData(salvo.inscricao_fim),
        salvo.fim_tem_horario ? formatarHora(salvo.inscricao_fim) : "", "Encerrado ou removido",
        "", "", "", salvo.url_origem ?? "", "",
      ];
    }
    const quem = listaDeProponentes(edital);
    const numero = (v) => (v == null ? NAO_INFORMADO : v.toFixed(2).replace(".", ","));
    return [
      edital.titulo, edital.orgao_fomento, formatarData(edital.inscricao_fim),
      edital.fim_tem_horario ? formatarHora(edital.inscricao_fim) : "",
      encerrado(edital, agora) ? "Encerrado" : "Aberto",
      edital.tipo_apoio === "cessao_de_espaco" ? "Cessão de espaço (sem repasse)" : numero(edital.valor_total),
      edital.tipo_apoio === "cessao_de_espaco" ? "" : numero(edital.teto_por_projeto),
      quem.length ? quem.join(", ") : NAO_INFORMADO,
      edital.url_origem, urlDoPortal(edital.chave),
    ];
  });
  return `\uFEFF${[cabecalho, ...linhas].map((l) => l.map(celulaCsv).join(";")).join("\r\n")}\r\n`;
}

function escaparIcs(texto) {
  return String(texto).replaceAll("\\", "\\\\").replaceAll(";", "\;").replaceAll(",", "\\,").replaceAll("\n", "\\n");
}

/** Dobra linhas em 75 octetos (RFC 5545), sem partir caracteres multibyte. */
function dobrarIcs(linha) {
  const codificador = new TextEncoder();
  const partes = [];
  let atual = "";
  let bytes = 0;
  for (const caractere of linha) {
    const tamanho = codificador.encode(caractere).length;
    const limite = partes.length === 0 ? 75 : 74;
    if (bytes + tamanho > limite) {
      partes.push(atual);
      atual = "";
      bytes = 0;
    }
    atual += caractere;
    bytes += tamanho;
  }
  partes.push(atual);
  return partes.join("\r\n ");
}

function carimboUtc(data) {
  return data.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
}

function dataIcs({ ano, mes, dia }) {
  return `${ano}${String(mes).padStart(2, "0")}${String(dia).padStart(2, "0")}`;
}

/** Um evento por favorito, no fim da inscrição. Sem horário informado = dia inteiro. */
export function gerarIcs(itens, urlDoPortal, agora = new Date()) {
  const linhas = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Editais Culturais//PT-BR//", "CALSCALE:GREGORIAN"];
  for (const { salvo, edital } of itens) {
    const base = edital ?? salvo;
    const fim = new Date(base.inscricao_fim);
    const titulo = `Fim da inscrição: ${base.titulo}`;
    const descricao = [
      base.orgao_fomento,
      edital ? `Prazo: ${prazoPorExtenso(edital)}` : `Prazo: ${formatarData(base.inscricao_fim)}`,
      base.url_origem ?? "",
      edital ? `No portal: ${urlDoPortal(edital.chave)}` : "",
    ].filter(Boolean).join("\n");
    linhas.push("BEGIN:VEVENT", `UID:${base.chave}@editais-culturais`, `DTSTAMP:${carimboUtc(agora)}`);
    if (base.fim_tem_horario) {
      linhas.push(`DTSTART:${carimboUtc(fim)}`, `DTEND:${carimboUtc(new Date(fim.getTime() + 30 * 60_000))}`);
    } else {
      const dia = partesBrasilia(fim);
      const seguinte = new Date(Date.UTC(dia.ano, dia.mes - 1, dia.dia + 1));
      linhas.push(
        `DTSTART;VALUE=DATE:${dataIcs(dia)}`,
        `DTEND;VALUE=DATE:${dataIcs({ ano: seguinte.getUTCFullYear(), mes: seguinte.getUTCMonth() + 1, dia: seguinte.getUTCDate() })}`,
      );
    }
    linhas.push(
      `SUMMARY:${escaparIcs(titulo)}`,
      `DESCRIPTION:${escaparIcs(descricao)}`,
      ...(base.url_origem ? [`URL:${base.url_origem}`] : []),
      "BEGIN:VALARM", "ACTION:DISPLAY", `DESCRIPTION:${escaparIcs(titulo)}`, "TRIGGER:-P1D", "END:VALARM",
      "END:VEVENT",
    );
  }
  linhas.push("END:VCALENDAR");
  return `${linhas.map(dobrarIcs).join("\r\n")}\r\n`;
}

/** Lista de favoritos em texto para o WhatsApp. */
export function gerarTextoWhatsapp(itens, urlDoPortal) {
  const blocos = itens.map(({ salvo, edital }) => {
    if (!edital) {
      return [`*${salvo.titulo}*`, salvo.orgao_fomento, "Encerrado ou removido", salvo.url_origem ?? ""]
        .filter(Boolean)
        .join("\n");
    }
    return [
      `*${edital.titulo}*`,
      edital.orgao_fomento,
      resumoDeValores(edital),
      `Prazo: até ${prazoPorExtenso(edital)}`,
      urlDoPortal(edital.chave),
    ].join("\n");
  });
  return [`Meus editais favoritos (${itens.length})`, ...blocos].join("\n\n");
}

export function baixarArquivo(nome, conteudo, tipo) {
  const url = URL.createObjectURL(new Blob([conteudo], { type: tipo }));
  const link = document.createElement("a");
  link.href = url;
  link.download = nome;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
