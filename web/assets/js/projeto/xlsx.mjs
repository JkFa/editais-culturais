// Planilha do projeto (.xlsx) montada no navegador com ExcelJS, sem servidor.
// As contas são FÓRMULAS de verdade (total do item, SUBTOTAL por etapa, resumo com SUMIF): quem mexer numa
// quantidade no Excel ou no LibreOffice vê os totais e os percentuais se recalcularem. Cada fórmula leva
// também o resultado já calculado, para visualizadores que não recalculam.
import { ETAPAS, ROTULOS_ETAPA, resumoOrcamento, totalDoItem } from "./tabelas.mjs";

export const EXCELJS_URL = "https://cdn.jsdelivr.net/npm/exceljs@4.4.0/dist/exceljs.min.js";
export const ABA = { orcamento: "Orçamento", resumo: "Resumo", cronograma: "Cronograma", midia: "Plano de mídia" };
const MOEDA = '"R$" #,##0.00';
const REF_ORC = `'${ABA.orcamento}'`;
const COR_TITULO = "FF1F2937";
const COR_CABECALHO = "FFE5E7EB";
const COR_MARCA = "FF1F6F43";
const COR_ALERTA = "FFFECACA";
const COR_AVISO = "FFFEF3C7";

const dataBr = (d) => d.toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" });
export const nomeDoArquivoXlsx = (titulo) => {
  const base = String(titulo ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
  return `planilhas-${base || "cultural"}.xlsx`;
};
const num = (v) => {
  if (v == null || String(v).trim() === "") return null; // vazio é "a confirmar", nunca zero
  const n = Number(String(v).replace(",", "."));
  return Number.isFinite(n) ? n : null;
};

function cabecalhoDaAba(ws, { projeto, agora }, titulo, colunas) {
  ws.getCell("A1").value = `${projeto.titulo} · ${titulo}`;
  ws.getCell("A1").font = { bold: true, size: 14, color: { argb: COR_TITULO } };
  ws.getCell("A2").value = `Edital: ${projeto.edital_titulo || "projeto avulso (sem edital)"}`;
  ws.getCell("A3").value = `Gerado em ${dataBr(agora)} pelo Assistente de Projetos Culturais. Confira os valores antes de enviar.`;
  ws.getCell("A3").font = { italic: true, color: { argb: "FF4B5563" } };
  const linha = ws.getRow(5);
  colunas.forEach((c, i) => {
    const cel = linha.getCell(i + 1);
    cel.value = c.titulo;
    cel.font = { bold: true };
    cel.fill = { type: "pattern", pattern: "solid", fgColor: { argb: COR_CABECALHO } };
    cel.alignment = { vertical: "middle", wrapText: true, horizontal: c.direita ? "right" : "left" };
    ws.getColumn(i + 1).width = c.largura;
  });
  linha.height = 22;
  ws.views = [{ state: "frozen", ySplit: 5 }]; // linha de cabeçalho congelada
}

function abaOrcamento(wb, dados) {
  const { orcamento } = dados;
  const ws = wb.addWorksheet(ABA.orcamento);
  cabecalhoDaAba(ws, dados, "Orçamento", [
    { titulo: "Etapa", largura: 16 }, { titulo: "Rubrica", largura: 18 }, { titulo: "Item", largura: 34 },
    { titulo: "Unidade", largura: 11 }, { titulo: "Qtd", largura: 8, direita: true }, { titulo: "Valor unit. (R$)", largura: 16, direita: true },
    { titulo: "Total (R$)", largura: 16, direita: true }, { titulo: "Fonte do preço", largura: 28 }, { titulo: "Justificativa", largura: 40 },
  ]);
  const linhaDoItem = new Map(); // id do item -> linha, para o plano de mídia apontar para o orçamento
  let r = 6;
  const subtotais = [];
  for (const etapa of ETAPAS) {
    const itens = orcamento.filter((i) => i.etapa === etapa);
    if (!itens.length) continue;
    const ini = r;
    for (const i of itens) {
      const q = num(i.quantidade) ?? 0;
      const v = num(i.valor_unitario);
      ws.getCell(`A${r}`).value = ROTULOS_ETAPA[etapa];
      ws.getCell(`B${r}`).value = i.rubrica || "";
      ws.getCell(`C${r}`).value = i.item;
      ws.getCell(`D${r}`).value = i.unidade || "";
      ws.getCell(`E${r}`).value = q;
      ws.getCell(`F${r}`).value = v; // vazio = valor a confirmar (destacado abaixo)
      ws.getCell(`G${r}`).value = { formula: `E${r}*F${r}`, result: totalDoItem(i) ?? 0 };
      ws.getCell(`H${r}`).value = i.fonte_preco || "";
      ws.getCell(`I${r}`).value = i.justificativa || "";
      ws.getCell(`F${r}`).numFmt = MOEDA;
      ws.getCell(`G${r}`).numFmt = MOEDA;
      ws.getCell(`E${r}`).numFmt = "#,##0.###";
      if (v == null || !String(i.fonte_preco ?? "").trim()) {
        for (const col of v == null ? ["F"] : ["H"]) ws.getCell(`${col}${r}`).fill = { type: "pattern", pattern: "solid", fgColor: { argb: COR_AVISO } };
      }
      ws.getCell(`C${r}`).alignment = { wrapText: true, vertical: "top" };
      ws.getCell(`I${r}`).alignment = { wrapText: true, vertical: "top" };
      if (i.id) linhaDoItem.set(i.id, r);
      r++;
    }
    const soma = itens.reduce((s, i) => s + (totalDoItem(i) ?? 0), 0);
    ws.getCell(`A${r}`).value = `Subtotal · ${ROTULOS_ETAPA[etapa]}`;
    ws.getCell(`G${r}`).value = { formula: `SUBTOTAL(9,G${ini}:G${r - 1})`, result: Math.round(soma * 100) / 100 };
    ws.getCell(`G${r}`).numFmt = MOEDA;
    for (const col of "ABCDEFGHI") {
      ws.getCell(`${col}${r}`).font = { bold: true };
      ws.getCell(`${col}${r}`).border = { top: { style: "thin" } };
    }
    subtotais.push(r);
    r++;
  }
  const total = resumoOrcamento(orcamento).total;
  ws.getCell(`A${r}`).value = "TOTAL GERAL";
  // SUBTOTAL ignora as linhas de subtotal: a soma de tudo não conta duas vezes
  ws.getCell(`G${r}`).value = { formula: `SUBTOTAL(9,G6:G${r - 1})`, result: total };
  ws.getCell(`G${r}`).numFmt = MOEDA;
  for (const col of "ABCDEFGHI") {
    ws.getCell(`${col}${r}`).font = { bold: true, size: 12 };
    ws.getCell(`${col}${r}`).border = { top: { style: "double" } };
  }
  if (!orcamento.length) ws.getCell("A6").value = "Nenhum item no orçamento ainda.";
  return { ws, linhaDoItem, linhaTotal: r, ultimaLinhaDeItens: r - 1 };
}

function abaResumo(wb, dados) {
  const { criterios, orcamento, ficha } = dados;
  const ws = wb.addWorksheet(ABA.resumo);
  cabecalhoDaAba(ws, dados, "Resumo", [
    { titulo: "Etapa", largura: 22 }, { titulo: "Total (R$)", largura: 18, direita: true }, { titulo: "% do total", largura: 12, direita: true },
    { titulo: "Máximo do edital", largura: 17, direita: true }, { titulo: "Mínimo do edital", largura: 17, direita: true }, { titulo: "Situação", largura: 22 },
  ]);
  const resumo = resumoOrcamento(orcamento);
  const max = criterios?.percentuais_max ?? {};
  const min = criterios?.percentuais_min ?? {};
  const primeira = 6;
  const ultima = primeira + ETAPAS.length - 1;
  const lt = ultima + 1; // linha do total
  ETAPAS.forEach((etapa, k) => {
    const r = primeira + k;
    const b = resumo.porEtapa[etapa];
    ws.getCell(`A${r}`).value = ROTULOS_ETAPA[etapa];
    ws.getCell(`B${r}`).value = { formula: `SUMIF(${REF_ORC}!$A:$A,A${r},${REF_ORC}!$G:$G)`, result: b.total };
    ws.getCell(`C${r}`).value = { formula: `IF($B$${lt}=0,0,B${r}/$B$${lt})`, result: b.percentual };
    ws.getCell(`D${r}`).value = max[etapa] != null ? Number(max[etapa]) : null;
    ws.getCell(`E${r}`).value = min[etapa] != null ? Number(min[etapa]) : null;
    const acima = max[etapa] != null && b.percentual > Number(max[etapa]) + 1e-9;
    const abaixo = min[etapa] != null && resumo.total > 0 && b.percentual < Number(min[etapa]) - 1e-9;
    ws.getCell(`F${r}`).value = {
      formula: `IF(AND(D${r}<>"",C${r}>D${r}),"Acima do limite",IF(AND(E${r}<>"",C${r}<E${r}),"Abaixo do mínimo","OK"))`,
      result: acima ? "Acima do limite" : abaixo ? "Abaixo do mínimo" : "OK",
    };
    ws.getCell(`B${r}`).numFmt = MOEDA;
    for (const col of "CDE") ws.getCell(`${col}${r}`).numFmt = "0.0%";
  });
  ws.getCell(`A${lt}`).value = "TOTAL";
  ws.getCell(`B${lt}`).value = { formula: `SUM(B${primeira}:B${ultima})`, result: resumo.total };
  ws.getCell(`C${lt}`).value = { formula: `SUM(C${primeira}:C${ultima})`, result: resumo.total > 0 ? 1 : 0 };
  ws.getCell(`B${lt}`).numFmt = MOEDA;
  ws.getCell(`C${lt}`).numFmt = "0.0%";
  for (const col of "ABCDEF") ws.getCell(`${col}${lt}`).font = { bold: true };

  const teto = Number(criterios?.teto_orcamento) || Number(ficha?.edital_teto_por_projeto) || null;
  const rt = lt + 2;
  ws.getCell(`A${rt}`).value = "Teto do edital";
  ws.getCell(`B${rt}`).value = teto;
  ws.getCell(`B${rt}`).numFmt = MOEDA;
  ws.getCell(`A${rt + 1}`).value = "Diferença (teto − total)";
  ws.getCell(`B${rt + 1}`).value = { formula: `IF(B${rt}="","",B${rt}-B${lt})`, result: teto ? Math.round((teto - resumo.total) * 100) / 100 : "" };
  ws.getCell(`B${rt + 1}`).numFmt = MOEDA;
  ws.getCell(`A${rt + 2}`).value = "Situação do total";
  ws.getCell(`B${rt + 2}`).value = {
    formula: `IF(B${rt}="","Sem teto informado",IF(B${lt}>B${rt},"Acima do teto","Dentro do teto"))`,
    result: !teto ? "Sem teto informado" : resumo.total > teto ? "Acima do teto" : "Dentro do teto",
  };
  ws.getCell(`A${rt + 4}`).value = "Valores em amarelo no orçamento ainda precisam ser confirmados (sem valor ou sem fonte do preço).";
  ws.getCell(`A${rt + 4}`).font = { italic: true };

  // destaque condicional: passou do teto ou do percentual
  const vermelho = { type: "pattern", pattern: "solid", bgColor: { argb: COR_ALERTA } };
  ws.addConditionalFormatting({ ref: `B${lt}`, rules: [{ type: "expression", formulae: [`AND($B$${rt}<>"",$B$${lt}>$B$${rt})`], style: { fill: vermelho, font: { bold: true, color: { argb: "FF991B1B" } } } }] });
  ws.addConditionalFormatting({ ref: `B${rt + 2}`, rules: [{ type: "expression", formulae: [`$B$${rt + 2}="Acima do teto"`], style: { fill: vermelho } }] });
  ws.addConditionalFormatting({ ref: `C${primeira}:C${ultima}`, rules: [{ type: "expression", formulae: [`AND($D${primeira}<>"",$C${primeira}>$D${primeira})`], style: { fill: vermelho } }] });
  ws.addConditionalFormatting({ ref: `F${primeira}:F${ultima}`, rules: [{ type: "expression", formulae: [`$F${primeira}<>"OK"`], style: { fill: vermelho } }] });
  return { ws, linhaTotal: lt, linhaTeto: rt };
}

function abaCronograma(wb, dados) {
  const { cronograma, criterios } = dados;
  const prazo = Number(criterios?.prazo_execucao_meses) || 0;
  const meses = Math.max(prazo || 12, ...cronograma.map((a) => Number(a.mes_fim) || 0));
  const ws = wb.addWorksheet(ABA.cronograma);
  cabecalhoDaAba(ws, dados, "Cronograma", [
    { titulo: "Atividade", largura: 36 }, { titulo: "Etapa", largura: 16 },
    ...Array.from({ length: meses }, (_, i) => ({ titulo: `Mês ${i + 1}`, largura: 7 })),
  ]);
  for (let m = 1; m <= meses; m++) {
    const cel = ws.getRow(5).getCell(m + 2);
    cel.alignment = { horizontal: "center", vertical: "middle", wrapText: true };
    if (prazo && m > prazo) cel.font = { bold: true, color: { argb: "FFB91C1C" } };
  }
  cronograma.forEach((a, k) => {
    const r = 6 + k;
    ws.getCell(`A${r}`).value = a.atividade;
    ws.getCell(`B${r}`).value = ROTULOS_ETAPA[a.etapa] ?? a.etapa;
    for (let m = Number(a.mes_inicio); m <= Number(a.mes_fim); m++) {
      const cel = ws.getRow(r).getCell(m + 2);
      cel.value = "●";
      cel.alignment = { horizontal: "center" };
      cel.font = { color: { argb: "FFFFFFFF" } };
      cel.fill = { type: "pattern", pattern: "solid", fgColor: { argb: prazo && m > prazo ? "FFB91C1C" : COR_MARCA } };
    }
  });
  if (!cronograma.length) ws.getCell("A6").value = "Nenhuma atividade no cronograma ainda.";
  return ws;
}

function abaMidia(wb, dados, linhaDoItem) {
  const ws = wb.addWorksheet(ABA.midia);
  cabecalhoDaAba(ws, dados, "Plano de mídia", [
    { titulo: "Peça", largura: 32 }, { titulo: "Canal", largura: 24 }, { titulo: "Período", largura: 20 },
    { titulo: "Quantidade", largura: 12, direita: true }, { titulo: "Custo (R$)", largura: 16, direita: true }, { titulo: "Item do orçamento", largura: 30 },
  ]);
  const porId = new Map(dados.orcamento.map((i) => [i.id, i]));
  dados.midia.forEach((p, k) => {
    const r = 6 + k;
    ws.getCell(`A${r}`).value = p.peca;
    ws.getCell(`B${r}`).value = p.canal || "";
    ws.getCell(`C${r}`).value = p.periodo || "";
    ws.getCell(`D${r}`).value = num(p.quantidade) ?? 1;
    const ligado = p.orcamento_item_id ? porId.get(p.orcamento_item_id) : null;
    const linha = ligado ? linhaDoItem.get(ligado.id) : null;
    // peça ligada ao orçamento: o custo é o do item (se o item mudar, a mídia acompanha)
    ws.getCell(`E${r}`).value = linha ? { formula: `${REF_ORC}!G${linha}`, result: totalDoItem(ligado) ?? 0 } : num(p.custo);
    ws.getCell(`E${r}`).numFmt = MOEDA;
    ws.getCell(`F${r}`).value = ligado ? ligado.item : "";
  });
  if (!dados.midia.length) ws.getCell("A6").value = "Nenhuma peça no plano de mídia ainda.";
  return ws;
}

/**
 * @param {*} ExcelJS biblioteca (global do CDN no navegador; `npm:exceljs` nos testes)
 * @param {{projeto:{titulo:string,edital_titulo?:string}, orcamento:object[], cronograma:object[], midia:object[], criterios?:object|null, ficha?:object, agora?:Date}} dados
 */
export function montarPlanilha(ExcelJS, dados) {
  const d = { criterios: null, ficha: {}, agora: new Date(), ...dados };
  const wb = new ExcelJS.Workbook();
  wb.creator = "Assistente de Projetos Culturais";
  wb.title = d.projeto.titulo;
  wb.created = d.agora;
  wb.calcProperties = { fullCalcOnLoad: true }; // o Excel recalcula ao abrir
  const orc = abaOrcamento(wb, d);
  abaResumo(wb, d);
  abaCronograma(wb, d);
  abaMidia(wb, d, orc.linhaDoItem);
  return wb;
}

/** Carrega o ExcelJS do CDN uma única vez (script clássico: o pacote é UMD). */
export function carregarExcelJS(doc = document, url = EXCELJS_URL) {
  if (globalThis.ExcelJS) return Promise.resolve(globalThis.ExcelJS);
  return new Promise((resolve, reject) => {
    const s = doc.createElement("script");
    s.src = url;
    s.async = true;
    s.onload = () => (globalThis.ExcelJS ? resolve(globalThis.ExcelJS) : reject(new Error("ExcelJS não carregou")));
    s.onerror = () => reject(new Error("Não consegui carregar a biblioteca de planilhas."));
    doc.head.append(s);
  });
}

export async function gerarXlsxBlob(dados, ExcelJS) {
  const lib = ExcelJS ?? (await carregarExcelJS());
  const buffer = await montarPlanilha(lib, dados).xlsx.writeBuffer();
  return new Blob([buffer], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
}
