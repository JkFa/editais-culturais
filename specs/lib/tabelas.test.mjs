import assert from "node:assert/strict";
import { test } from "node:test";
import { lerNumero, normalizarLinha, resumoOrcamento, totalDoItem, valorAConfirmar } from "./tabelas.mjs";

test("lerNumero aceita formatos brasileiros e rejeita lixo", () => {
  assert.equal(lerNumero("1.500,50"), 1500.5);
  assert.equal(lerNumero("R$ 2.000"), 2000);
  assert.equal(lerNumero("2000"), 2000);
  assert.equal(lerNumero("12,5"), 12.5);
  assert.equal(lerNumero(""), null);
  assert.equal(lerNumero(null), null);
  assert.ok(Number.isNaN(lerNumero("abc")));
  assert.ok(Number.isNaN(lerNumero(Infinity)));
});

test("normalizarLinha descarta campos desconhecidos e id inválido", () => {
  const { linha, erros } = normalizarLinha("orcamento_itens", {
    id: "não-é-uuid",
    projeto_id: "00000000-0000-0000-0000-000000000000",
    user_id: "x",
    etapa: "producao",
    item: "  Cachê   do   diretor ",
    quantidade: "2",
    valor_unitario: "1.000,00",
  });
  assert.deepEqual(erros, {});
  assert.equal(linha.item, "Cachê do diretor");
  assert.equal(linha.quantidade, 2);
  assert.equal(linha.valor_unitario, 1000);
  assert.equal(linha.id, undefined);
  assert.equal(linha.projeto_id, undefined);
  assert.equal(linha.user_id, undefined);
  const bom = normalizarLinha("orcamento_itens", { id: "0F8FAD5B-D9CB-469F-A165-70867728950E", etapa: "producao", item: "x" });
  assert.equal(bom.linha.id, "0f8fad5b-d9cb-469f-a165-70867728950e");
});

test("erros por campo: obrigatório, enum, negativo, tamanho e meses", () => {
  const o = normalizarLinha("orcamento_itens", { etapa: "luxo", item: "", quantidade: -1, valor_unitario: "abc" });
  assert.deepEqual(Object.keys(o.erros).sort(), ["etapa", "item", "quantidade", "valor_unitario"]);
  assert.equal(normalizarLinha("orcamento_itens", { etapa: "producao", item: "x".repeat(201) }).erros.item, "Use até 200 caracteres.");
  const c = normalizarLinha("cronograma_itens", { etapa: "producao", atividade: "a", mes_inicio: 5, mes_fim: 2 });
  assert.equal(c.erros.mes_fim, "O mês final não pode vir antes do inicial.");
  assert.ok(normalizarLinha("cronograma_itens", { etapa: "producao", atividade: "a", mes_inicio: 0, mes_fim: 2 }).erros.mes_inicio);
  assert.ok(normalizarLinha("cronograma_itens", { etapa: "producao", atividade: "a", mes_inicio: 1.5, mes_fim: 2 }).erros.mes_inicio);
  assert.throws(() => normalizarLinha("usuarios", {}));
});

test("valor nulo é permitido (valor a confirmar) e vazio vira o padrão", () => {
  const { linha, erros } = normalizarLinha("orcamento_itens", { etapa: "divulgacao", item: "Cartaz" });
  assert.deepEqual(erros, {});
  assert.equal(linha.valor_unitario, null);
  assert.equal(linha.quantidade, 1);
  assert.equal(linha.unidade, "unidade");
  assert.equal(linha.fonte_preco, null);
});

test("equipe só guarda uuids válidos das ligações com o orçamento", () => {
  const ok = "0f8fad5b-d9cb-469f-a165-70867728950e";
  const { linha } = normalizarLinha("equipe_membros", { funcao: "Direção", orcamento_item_ids: [ok, "lixo", ok] });
  assert.deepEqual(linha.orcamento_item_ids, [ok]);
  assert.equal(normalizarLinha("equipe_membros", { funcao: "" }).erros.funcao, "Preencha este campo.");
});

test("total do item e resumo por etapa com percentuais", () => {
  assert.equal(totalDoItem({ quantidade: 3, valor_unitario: 33.33 }), 99.99);
  assert.equal(totalDoItem({ quantidade: 2, valor_unitario: null }), null);
  const itens = [
    { etapa: "producao", quantidade: 1, valor_unitario: 7000 },
    { etapa: "divulgacao", quantidade: 2, valor_unitario: 1000 },
    { etapa: "divulgacao", quantidade: 1, valor_unitario: null },
  ];
  const r = resumoOrcamento(itens);
  assert.equal(r.total, 9000);
  assert.equal(r.semValor, 1);
  assert.equal(r.porEtapa.divulgacao.total, 2000);
  assert.ok(Math.abs(r.porEtapa.divulgacao.percentual - 2000 / 9000) < 1e-9);
  assert.equal(r.porEtapa.acessibilidade.total, 0);
});

test("valor a confirmar: sem valor OU sem fonte do preço", () => {
  assert.equal(valorAConfirmar({ quantidade: 1, valor_unitario: 100, fonte_preco: "" }), true);
  assert.equal(valorAConfirmar({ quantidade: 1, valor_unitario: null, fonte_preco: "sindicato" }), true);
  assert.equal(valorAConfirmar({ quantidade: 1, valor_unitario: 100, fonte_preco: "sindicato" }), false);
});
