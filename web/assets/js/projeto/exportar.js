// Exportação: .docx no navegador (biblioteca `docx`, carregada só quando se clica) e "copiar por campo".
import { el } from "../formatos.js";
import { dialogo } from "./proponente.js";
import { camposParaCopiar, nomeDoArquivo, paragrafos, partirTexto, resumosParaDocx, tabelaParaTexto, textoParaCopiar } from "./texto-docx.js";
import { gerarXlsxBlob, nomeDoArquivoXlsx } from "./xlsx.mjs";

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

export async function gerarDocx({ projeto, secoes, linhaDe, proponente, tabelas = null }) {
  const { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, PageBreak, Table, TableRow, TableCell, WidthType } = await import(DOCX_URL);
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
  // Tabelas-resumo (orçamento por etapa e cronograma), depois das seções.
  const resumos = tabelas ? resumosParaDocx(tabelas) : { orcamento: null, cronograma: null };
  const desenhar = (titulo, r) => {
    const celula = (t, negrito = false) => new TableCell({ children: [new Paragraph({ children: [new TextRun({ text: String(t), bold: negrito, size: 22 })] })] });
    return [
      new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 360, after: 160 }, children: [new TextRun(titulo)] }),
      new Table({
        width: { size: 100, type: WidthType.PERCENTAGE },
        rows: [
          new TableRow({ tableHeader: true, children: r.cabecalho.map((c) => celula(c, true)) }),
          ...r.linhas.map((l, i) => new TableRow({ children: l.map((c) => celula(c, titulo.startsWith("Orçamento") && i === r.linhas.length - 1)) })),
        ],
      }),
      ...(r.aviso ? [new Paragraph({ spacing: { before: 120 }, children: runs(`${r.aviso}`, { size: 22, italics: true }) })] : []),
    ];
  };
  const anexos = [
    ...(resumos.orcamento ? desenhar("Orçamento por etapa (resumo)", resumos.orcamento) : []),
    ...(resumos.cronograma ? desenhar("Cronograma (resumo)", resumos.cronograma) : []),
  ];
  const doc = new Document({ creator: "Assistente de Projetos Culturais", title: projeto.titulo, sections: [{ children: [...capa, ...corpo, ...anexos] }] });
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

const NOME_DA_TABELA = { orcamento: "orcamento_itens", cronograma: "cronograma_itens", midia: "midia_itens", equipe: "equipe_membros" };
const valida = (tabela, l) => String(l[{ orcamento_itens: "item", cronograma_itens: "atividade", midia_itens: "peca", equipe_membros: "funcao" }[tabela]] ?? "").trim() !== "";

export async function abrirExportacao(ctx, { projeto, secoes, linhaDe, tabelas = () => null, criterios = null }) {
  const dadosDasTabelas = () => {
    const t = tabelas();
    if (!t) return null;
    return Object.fromEntries(Object.entries(t).map(([k, v]) => [k, v.filter((l) => valida(NOME_DA_TABELA[k], l))]));
  };
  const aprovadas = secoes.filter((s) => linhaDe(s.id)?.aprovado && linhaDe(s.id)?.rascunho);
  const msg = el("p", { class: "msg-form", role: "status" });
  const botaoDocx = el("button", { type: "button", class: "btn btn-primario", disabled: aprovadas.length === 0 || null }, "Baixar .docx");
  botaoDocx.addEventListener("click", async () => {
    botaoDocx.disabled = true;
    msg.className = "msg-form";
    msg.textContent = "Gerando o arquivo…";
    try {
      const proponente = await ctx.api.proponente(projeto.id).catch(() => ({}));
      const blob = await gerarDocx({ projeto, secoes: aprovadas, linhaDe, proponente, tabelas: dadosDasTabelas() });
      baixar(blob, nomeDoArquivo(projeto.titulo));
      msg.textContent = "Arquivo gerado. Os pontos [COMPLETAR] estão destacados em amarelo.";
    } catch {
      msg.className = "msg-form erro";
      msg.textContent = "Não consegui gerar o .docx agora (a biblioteca não carregou). Confira sua internet ou use \"Copiar por campo\".";
    } finally {
      botaoDocx.disabled = false;
    }
  });

  const msgXlsx = el("p", { class: "msg-form", role: "status" });
  const botaoXlsx = el("button", { type: "button", class: "btn btn-primario" }, "Baixar planilhas (.xlsx)");
  botaoXlsx.addEventListener("click", async () => {
    botaoXlsx.disabled = true;
    msgXlsx.className = "msg-form";
    msgXlsx.textContent = "Gerando a planilha…";
    try {
      const t = dadosDasTabelas() ?? { orcamento: [], cronograma: [], midia: [] };
      const blob = await gerarXlsxBlob({ projeto, orcamento: t.orcamento ?? [], cronograma: t.cronograma ?? [], midia: t.midia ?? [], criterios, ficha: projeto.ficha ?? {}, agora: new Date() });
      baixar(blob, nomeDoArquivoXlsx(projeto.titulo));
      msgXlsx.textContent = "Planilha gerada. Os totais são fórmulas: se você mudar uma quantidade, tudo se recalcula.";
    } catch {
      msgXlsx.className = "msg-form erro";
      msgXlsx.textContent = "Não consegui gerar a planilha agora (a biblioteca não carregou). Confira sua internet e tente de novo.";
    } finally {
      botaoXlsx.disabled = false;
    }
  });

  const { doEdital, campos: camposCopia } = camposParaCopiar({ secoes, linhaDe, criterios });
  const campos = camposCopia.map((c) => {
    const retorno = el("span", { class: "msg-form", role: "status" });
    const botao = el("button", { type: "button", class: "btn", disabled: !c.texto || null }, "Copiar");
    botao.addEventListener("click", async () => {
      retorno.textContent = (await copiar(textoParaCopiar(c.texto))) ? "Copiado." : "Não consegui copiar; selecione o texto no painel.";
    });
    return el("li", { class: "campo-copia" },
      el("div", {}, el("strong", {}, c.nome), " ", el("span", { class: "status" }, c.semSecao ? "sem seção correspondente no assistente" : c.aprovada ? "aprovada" : c.texto ? "rascunho" : "sem texto"),
        c.limite ? el("p", { class: `contador${c.texto.length > c.limite ? " excedeu" : ""}` }, el("span", { class: "num" }, `${c.texto.length} / ${c.limite}`), " caracteres") : null),
      el("div", {}, botao, " ", retorno));
  });

  // Tabelas em texto (colar numa plataforma ou planilha).
  const tabelasTexto = [["orcamento_itens", "orcamento", "Orçamento"], ["cronograma_itens", "cronograma", "Cronograma"], ["midia_itens", "midia", "Plano de mídia"], ["equipe_membros", "equipe", "Equipe"]].map(([tab, chave, nome]) => {
    const linhas = dadosDasTabelas()?.[chave] ?? [];
    const retorno = el("span", { class: "msg-form", role: "status" });
    const botao = el("button", { type: "button", class: "btn", disabled: !linhas.length || null }, "Copiar tabela");
    botao.addEventListener("click", async () => {
      retorno.textContent = (await copiar(tabelaParaTexto(tab, linhas))) ? "Copiado (colunas separadas por tabulação)." : "Não consegui copiar.";
    });
    return el("li", { class: "campo-copia" }, el("div", {}, el("strong", {}, nome), " ", el("span", { class: "status" }, linhas.length ? `${linhas.length} linha(s)` : "vazia")), el("div", {}, botao, " ", retorno));
  });

  dialogo("Exportar e copiar", el("div", {},
    el("h3", {}, "Arquivo .docx"),
    el("p", { class: "nota" }, aprovadas.length ? `Inclui as ${aprovadas.length} seções aprovadas, em ordem, com capa.` : "Aprove ao menos uma seção para gerar o arquivo."),
    botaoDocx, msg,
    el("h3", {}, "Planilhas (.xlsx)"),
    el("p", { class: "nota" }, "Quatro abas: Orçamento, Resumo (totais por etapa e comparação com o teto), Cronograma e Plano de mídia, com fórmulas que se recalculam."),
    botaoXlsx, msgXlsx,
    el("h3", {}, "Copiar por campo"),
    el("p", { class: "nota" }, doEdital
      ? "Na ordem e com os nomes dos campos do edital (lidos automaticamente: confira no edital). Os limites de caracteres são os do edital, quando existem."
      : "Para colar em plataformas de inscrição, como Prosas ou Mapas Culturais. Confira o limite de caracteres do edital."),
    el("ul", { class: "campos-copia" }, campos),
    el("h3", {}, "Copiar tabelas"),
    el("ul", { class: "campos-copia" }, tabelasTexto)));
}
