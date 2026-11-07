// Exportação: .docx no navegador (biblioteca `docx`, carregada só quando se clica) e "copiar por campo".
import { el } from "../formatos.js";
import { dialogo } from "./proponente.js";
import { nomeDoArquivo, paragrafos, partirTexto, textoParaCopiar } from "./texto-docx.js";

const DOCX_URL = "https://cdn.jsdelivr.net/npm/docx@9.5.0/+esm";

async function copiar(texto) {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch {
    const t = el("textarea", { "aria-hidden": "true", style: "position:fixed;opacity:0" });
    t.value = texto;
    document.body.append(t);
    t.select();
    const ok = document.execCommand?.("copy") ?? false;
    t.remove();
    return ok;
  }
}

export async function gerarDocx({ projeto, secoes, linhaDe, proponente }) {
  const { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, PageBreak } = await import(DOCX_URL);
  const runs = (texto, extra = {}) => partirTexto(texto).map((p) => new TextRun({ text: p.texto, ...extra, ...(p.completar ? { highlight: "yellow" } : {}) }));
  const nome = proponente?.nome_completo?.trim();
  const capa = [
    new Paragraph({ heading: HeadingLevel.TITLE, alignment: AlignmentType.CENTER, spacing: { before: 3000, after: 600 }, children: [new TextRun(projeto.titulo)] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: runs(`Proponente: ${nome || "[COMPLETAR: nome do proponente]"}`, { size: 26 }) }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: runs(`Edital: ${projeto.edital_titulo || "projeto avulso"}`, { size: 26 }) }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: runs(`Linguagem: ${projeto.ficha?.linguagem || "[COMPLETAR: linguagem artística]"}`, { size: 26 }) }),
    new Paragraph({ children: [new PageBreak()] }),
  ];
  const corpo = secoes.flatMap((s) => {
    const texto = linhaDe(s.id)?.rascunho ?? "";
    return [
      new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 360, after: 160 }, children: [new TextRun(s.titulo)] }),
      ...paragrafos(texto).map((p) => new Paragraph({ spacing: { after: 160, line: 320 }, children: runs(p, { size: 24 }) })),
    ];
  });
  const doc = new Document({ creator: "Assistente de Projetos Culturais", title: projeto.titulo, sections: [{ children: [...capa, ...corpo] }] });
  return Packer.toBlob(doc);
}

function baixar(blob, nome) {
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: nome });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export async function abrirExportacao(ctx, { projeto, secoes, linhaDe }) {
  const aprovadas = secoes.filter((s) => linhaDe(s.id)?.aprovado && linhaDe(s.id)?.rascunho);
  const msg = el("p", { class: "msg-form", role: "status" });
  const botaoDocx = el("button", { type: "button", class: "btn btn-primario", disabled: aprovadas.length === 0 || null }, "Baixar .docx");
  botaoDocx.addEventListener("click", async () => {
    botaoDocx.disabled = true;
    msg.className = "msg-form";
    msg.textContent = "Gerando o arquivo…";
    try {
      const proponente = await ctx.api.proponente(projeto.id).catch(() => ({}));
      const blob = await gerarDocx({ projeto, secoes: aprovadas, linhaDe, proponente });
      baixar(blob, nomeDoArquivo(projeto.titulo));
      msg.textContent = "Arquivo gerado. Os pontos [COMPLETAR] estão destacados em amarelo.";
    } catch {
      msg.className = "msg-form erro";
      msg.textContent = "Não consegui gerar o .docx agora (a biblioteca não carregou). Confira sua internet ou use \"Copiar por campo\".";
    } finally {
      botaoDocx.disabled = false;
    }
  });

  const campos = secoes.map((s) => {
    const l = linhaDe(s.id);
    const texto = l?.rascunho ?? "";
    const limite = s.limite_caracteres_padrao;
    const retorno = el("span", { class: "msg-form", role: "status" });
    const botao = el("button", { type: "button", class: "btn", disabled: !texto || null }, "Copiar");
    botao.addEventListener("click", async () => {
      retorno.textContent = (await copiar(textoParaCopiar(texto))) ? "Copiado." : "Não consegui copiar; selecione o texto no painel.";
    });
    return el("li", { class: "campo-copia" },
      el("div", {}, el("strong", {}, s.titulo), " ", el("span", { class: "status" }, l?.aprovado ? "aprovada" : texto ? "rascunho" : "sem texto"),
        el("p", { class: `contador${texto.length > limite ? " excedeu" : ""}` }, el("span", { class: "num" }, `${texto.length} / ${limite}`), " caracteres")),
      el("div", {}, botao, " ", retorno));
  });

  dialogo("Exportar e copiar", el("div", {},
    el("h3", {}, "Arquivo .docx"),
    el("p", { class: "nota" }, aprovadas.length ? `Inclui as ${aprovadas.length} seções aprovadas, em ordem, com capa.` : "Aprove ao menos uma seção para gerar o arquivo."),
    botaoDocx, msg,
    el("h3", {}, "Copiar por campo"),
    el("p", { class: "nota" }, "Para colar em plataformas de inscrição, como Prosas ou Mapas Culturais. Confira o limite de caracteres do edital."),
    el("ul", { class: "campos-copia" }, campos)));
}
