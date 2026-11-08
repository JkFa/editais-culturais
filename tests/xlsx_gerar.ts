// @ts-nocheck: script de teste (Deno). Gera a planilha SEM resultados em cache e com a quantidade pedida,
// para o LibreOffice ser obrigado a calcular as fórmulas de verdade.
import ExcelJS from "npm:exceljs@4.4.0";
import { montarPlanilha } from "../web/assets/js/projeto/xlsx.mjs";

const [saida, quantidade] = Deno.args;
const dados = {
  projeto: { titulo: "Oficinas de teatro de rua", edital_titulo: "Edital 001/2026" },
  orcamento: [
    { id: "a1", etapa: "producao", rubrica: "Equipe", item: "Cachê da direção", unidade: "mês", quantidade: 2, valor_unitario: 3000, fonte_preco: "sindicato", justificativa: "x" },
    { id: "a2", etapa: "producao", rubrica: "Materiais", item: "Figurino", unidade: "unidade", quantidade: 1, valor_unitario: null, fonte_preco: null, justificativa: "x" },
    { id: "a3", etapa: "divulgacao", rubrica: "Divulgação", item: "Impulsionamento", unidade: "unidade", quantidade: 1, valor_unitario: 1500, fonte_preco: "x", justificativa: "x" },
    { id: "a4", etapa: "acessibilidade", rubrica: "Acessibilidade", item: "Libras", unidade: "hora", quantidade: 10, valor_unitario: 100, fonte_preco: "x", justificativa: "x" },
  ],
  cronograma: [{ atividade: "Ensaios", etapa: "producao", mes_inicio: 2, mes_fim: 4 }],
  midia: [{ peca: "Posts", canal: "Instagram", periodo: "antes", quantidade: 4, custo: null, orcamento_item_id: "a3" }],
  criterios: { teto_orcamento: 12000, percentuais_max: { divulgacao: 0.15 } },
  ficha: {},
  agora: new Date("2026-11-08T15:00:00Z"),
};
const wb = montarPlanilha(ExcelJS, dados);
for (const ws of wb.worksheets) {
  ws.eachRow((row) => row.eachCell((c) => {
    if (c.value && typeof c.value === "object" && "formula" in c.value) c.value = { formula: c.value.formula };
  }));
}
wb.getWorksheet("Orçamento").getCell("E6").value = Number(quantidade); // "o usuário mexe numa quantidade"
await wb.xlsx.writeFile(saida);
