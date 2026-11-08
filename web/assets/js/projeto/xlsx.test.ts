// @ts-nocheck: tipos dos pacotes npm (exceljs, jszip) variam entre versões do Deno; o que importa aqui é o comportamento
// Planilha .xlsx: gera com a biblioteca REAL (ExcelJS), reabre e confere fórmulas, totais e formatação.
// Rodar: deno test --allow-read --allow-env --allow-net --allow-sys web/assets/js/projeto/xlsx.test.ts
import assert from "node:assert/strict";
import ExcelJS from "npm:exceljs@4.4.0";
import JSZip from "npm:jszip@3.10.1";
// @ts-ignore: módulo JS do navegador
import { ABA, montarPlanilha, nomeDoArquivoXlsx } from "./xlsx.mjs";

const ID = (n: number) => `00000000-0000-4000-8000-00000000000${n}`;
const orcamento = [
  { id: ID(1), etapa: "producao", rubrica: "Equipe", item: "Cachê da direção", unidade: "mês", quantidade: 2, valor_unitario: 3000, fonte_preco: "tabela do sindicato", justificativa: "Direção" },
  { id: ID(2), etapa: "producao", rubrica: "Materiais", item: "Figurino", unidade: "unidade", quantidade: 1, valor_unitario: null, fonte_preco: null, justificativa: "Cena" },
  { id: ID(3), etapa: "divulgacao", rubrica: "Divulgação", item: "Impulsionamento", unidade: "unidade", quantidade: 1, valor_unitario: 1500, fonte_preco: "", justificativa: "Alcance" },
  { id: ID(4), etapa: "acessibilidade", rubrica: "Acessibilidade", item: "Intérprete de Libras", unidade: "hora", quantidade: 10, valor_unitario: 100, fonte_preco: "orçamento", justificativa: "Libras" },
];
const dados = {
  projeto: { titulo: "Oficinas de teatro de rua", edital_titulo: "Edital 001/2026" },
  orcamento,
  cronograma: [
    { atividade: "Ensaios", etapa: "producao", mes_inicio: 2, mes_fim: 4 },
    { atividade: "Prestação de contas", etapa: "administrativo", mes_inicio: 7, mes_fim: 7 },
  ],
  midia: [
    { peca: "Posts patrocinados", canal: "Instagram", periodo: "antes", quantidade: 4, custo: null, orcamento_item_id: ID(3) },
    { peca: "Cartaz", canal: "Rua", periodo: "antes", quantidade: 100, custo: 250, orcamento_item_id: null },
  ],
  criterios: { teto_orcamento: 8000, percentuais_max: { divulgacao: 0.15 }, percentuais_min: { acessibilidade: 0.05 }, prazo_execucao_meses: 6 },
  ficha: {},
  agora: new Date("2026-11-08T15:00:00Z"),
};

async function ciclo() {
  const wb = montarPlanilha(ExcelJS, dados);
  const buffer = await wb.xlsx.writeBuffer(); // grava de verdade
  const lido = new ExcelJS.Workbook();
  await lido.xlsx.load(buffer); // e reabre
  return lido;
}
const f = (cel: ExcelJS.Cell) => (cel.value as { formula?: string })?.formula;
const r = (cel: ExcelJS.Cell) => (cel.value as { result?: unknown })?.result;

Deno.test("quatro abas na ordem pedida, com cabeçalho do projeto, título e data", async () => {
  const wb = await ciclo();
  assert.deepEqual(wb.worksheets.map((w) => w.name), [ABA.orcamento, ABA.resumo, ABA.cronograma, ABA.midia]);
  for (const ws of wb.worksheets) {
    assert.match(String(ws.getCell("A1").value), /Oficinas de teatro de rua/);
    assert.equal(ws.getCell("A2").value, "Edital: Edital 001/2026");
    assert.match(String(ws.getCell("A3").value), /Gerado em 08\/11\/2026/);
    assert.equal(ws.views[0].state, "frozen", `${ws.name}: cabeçalho congelado`);
    assert.equal((ws.views[0] as { ySplit: number }).ySplit, 5);
    assert.ok(ws.getColumn(1).width! >= 10, "largura ajustada");
    assert.equal(ws.getCell("A5").font?.bold, true);
  }
});

Deno.test("Orçamento: total do item é fórmula Qtd×Valor, subtotais com SUBTOTAL e total geral", async () => {
  const ws = (await ciclo()).getWorksheet(ABA.orcamento)!;
  // etapas na ordem: Produção (linhas 6-7), subtotal (8), Divulgação (9), subtotal (10), Acessibilidade (11), subtotal (12), total (13)
  assert.equal(ws.getCell("A6").value, "Produção");
  assert.equal(f(ws.getCell("G6")), "E6*F6");
  assert.equal(r(ws.getCell("G6")), 6000);
  assert.equal(f(ws.getCell("G7")), "E7*F7");
  assert.equal(ws.getCell("F7").value, null, "valor a confirmar fica vazio");
  assert.equal(f(ws.getCell("G8")), "SUBTOTAL(9,G6:G7)");
  assert.equal(r(ws.getCell("G8")), 6000);
  assert.equal(f(ws.getCell("G10")), "SUBTOTAL(9,G9:G9)");
  assert.equal(f(ws.getCell("G12")), "SUBTOTAL(9,G11:G11)");
  assert.equal(ws.getCell("A13").value, "TOTAL GERAL");
  assert.equal(f(ws.getCell("G13")), "SUBTOTAL(9,G6:G12)");
  assert.equal(r(ws.getCell("G13")), 8500);
  // formato de moeda
  for (const c of ["F6", "G6", "G13"]) assert.match(ws.getCell(c).numFmt!, /R\$/, c);
  // sem valor => amarelo; com valor mas sem fonte => fonte amarela
  assert.equal((ws.getCell("F7").fill as { fgColor: { argb: string } }).fgColor.argb, "FFFEF3C7");
  assert.equal((ws.getCell("H9").fill as { fgColor: { argb: string } }).fgColor.argb, "FFFEF3C7");
});

Deno.test("Resumo: total por etapa e % por fórmulas que apontam para a aba Orçamento", async () => {
  const ws = (await ciclo()).getWorksheet(ABA.resumo)!;
  assert.equal(ws.getCell("A7").value, "Produção"); // 2ª etapa (pré-produção é a 1ª)
  assert.equal(f(ws.getCell("B7")), "SUMIF('Orçamento'!$A:$A,A7,'Orçamento'!$G:$G)");
  assert.equal(r(ws.getCell("B7")), 6000);
  assert.equal(f(ws.getCell("C7")), "IF($B$12=0,0,B7/$B$12)");
  assert.ok(Math.abs((r(ws.getCell("C7")) as number) - 6000 / 8500) < 1e-9);
  assert.equal(ws.getCell("A12").value, "TOTAL");
  assert.equal(f(ws.getCell("B12")), "SUM(B6:B11)");
  assert.equal(r(ws.getCell("B12")), 8500);
  // limites do edital e situação
  assert.equal(ws.getCell("D9").value, 0.15); // divulgação máx
  assert.equal(ws.getCell("E10").value, 0.05); // acessibilidade mín
  assert.equal(r(ws.getCell("F9")), "Acima do limite"); // 1500/8500 = 17,6% > 15%
  assert.equal(r(ws.getCell("F10")), "OK"); // 1000/8500 = 11,8% >= 5%
  // teto, diferença e situação
  assert.equal(ws.getCell("A14").value, "Teto do edital");
  assert.equal(ws.getCell("B14").value, 8000);
  assert.equal(f(ws.getCell("B15")), 'IF(B14="","",B14-B12)');
  assert.equal(r(ws.getCell("B15")), -500);
  assert.equal(r(ws.getCell("B16")), "Acima do teto");
});

Deno.test("Resumo: destaque condicional quando passa do teto ou do percentual", async () => {
  const ws = (await ciclo()).getWorksheet(ABA.resumo)!;
  const cf = (ws as unknown as { conditionalFormattings: Array<{ ref: string; rules: Array<{ formulae: string[] }> }> }).conditionalFormattings;
  const refs = cf.map((x) => x.ref).sort();
  assert.deepEqual(refs, ["B12", "B16", "C6:C11", "F6:F11"]);
  assert.match(cf.find((x) => x.ref === "B12")!.rules[0].formulae[0], /\$B\$12>\$B\$14/);
  assert.match(cf.find((x) => x.ref === "C6:C11")!.rules[0].formulae[0], /\$C6>\$D6/);
});

Deno.test("Resumo sem critérios nem teto: situação 'Sem teto informado' e sem erro de fórmula", async () => {
  const wb = montarPlanilha(ExcelJS, { ...dados, criterios: null });
  const ws = wb.getWorksheet(ABA.resumo)!;
  assert.equal(ws.getCell("B14").value, null);
  assert.equal(r(ws.getCell("B16")), "Sem teto informado");
  assert.equal(r(ws.getCell("F9")), "OK");
});

Deno.test("Cronograma: mês 1..N (N = prazo do edital ou 12), células marcadas com preenchimento colorido", async () => {
  const ws = (await ciclo()).getWorksheet(ABA.cronograma)!;
  assert.equal(ws.getCell("C5").value, "Mês 1");
  assert.equal(ws.getRow(5).getCell(9).value, "Mês 7", "N = o maior mês usado (7), pois passa do prazo de 6");
  assert.equal(ws.getRow(5).getCell(10).value, null);
  assert.equal(ws.getCell("A6").value, "Ensaios");
  assert.equal(ws.getCell("B6").value, "Produção");
  // Ensaios: meses 2 a 4 => colunas D, E, F
  for (const [col, marcado] of [["C", false], ["D", true], ["E", true], ["F", true], ["G", false]] as const) {
    const cel = ws.getCell(`${col}6`);
    assert.equal(cel.value === "●", marcado, `${col}6`);
    assert.equal(Boolean(cel.fill && (cel.fill as { fgColor?: { argb: string } }).fgColor), marcado, `${col}6 preenchimento`);
  }
  // prestação no mês 7, depois do prazo de 6 meses: vermelho e N cresce até 12
  assert.equal((ws.getCell("I7").fill as { fgColor: { argb: string } }).fgColor.argb, "FFB91C1C");
  assert.equal(ws.getRow(5).getCell(9).font?.color?.argb, "FFB91C1C", "mês 7, fora do prazo, em vermelho");
  assert.notEqual(ws.getRow(5).getCell(8).font?.color?.argb, "FFB91C1C", "mês 6 está dentro do prazo");
});

Deno.test("Plano de mídia: peça ligada ao orçamento usa fórmula; a avulsa, o custo digitado", async () => {
  const ws = (await ciclo()).getWorksheet(ABA.midia)!;
  assert.equal(ws.getCell("A6").value, "Posts patrocinados");
  assert.equal(f(ws.getCell("E6")), "'Orçamento'!G9");
  assert.equal(r(ws.getCell("E6")), 1500);
  assert.equal(ws.getCell("F6").value, "Impulsionamento");
  assert.equal(ws.getCell("E7").value, 250);
  assert.match(ws.getCell("E7").numFmt!, /R\$/);
});

Deno.test("projeto vazio gera uma planilha válida (sem fórmulas quebradas)", async () => {
  const wb = montarPlanilha(ExcelJS, { projeto: { titulo: "Vazio" }, orcamento: [], cronograma: [], midia: [] });
  const buf = await wb.xlsx.writeBuffer();
  const lido = new ExcelJS.Workbook();
  await lido.xlsx.load(buf);
  assert.equal(lido.getWorksheet(ABA.orcamento)!.getCell("A6").value, "Nenhum item no orçamento ainda.");
  assert.equal(f(lido.getWorksheet(ABA.resumo)!.getCell("B12")), "SUM(B6:B11)");
  // o Excel recalcula ao abrir: a marca está no XML do arquivo
  const zip = await JSZip.loadAsync(buf);
  assert.match(await zip.file("xl/workbook.xml")!.async("string"), /fullCalcOnLoad="1"/);
});

Deno.test("nome do arquivo", () => {
  assert.equal(nomeDoArquivoXlsx("Oficinas de Teatro de Rua — 2026!"), "planilhas-oficinas-de-teatro-de-rua-2026.xlsx");
  assert.equal(nomeDoArquivoXlsx(""), "planilhas-cultural.xlsx");
});
