import assert from "node:assert/strict";
import { test } from "node:test";
import { camposParaCopiar, resumosParaDocx, tabelaParaTexto } from "../../web/assets/js/projeto/texto-docx.js";

const secoes = [
  { id: "ficha", titulo: "Ficha do projeto", limite_caracteres_padrao: 1500 },
  { id: "apresentacao", titulo: "Apresentação", limite_caracteres_padrao: 1500 },
  { id: "orcamento", titulo: "Orçamento", limite_caracteres_padrao: 2500 },
];
const linhas = { apresentacao: { rascunho: "Texto", aprovado: true } };
const linhaDe = (id) => linhas[id] ?? null;

test("sem campos do edital: ordem do assistente e limites padrão", () => {
  const r = camposParaCopiar({ secoes, linhaDe, criterios: null });
  assert.equal(r.doEdital, false);
  assert.deepEqual(r.campos.map((c) => [c.nome, c.limite]), [["Ficha do projeto", 1500], ["Apresentação", 1500], ["Orçamento", 2500]]);
  assert.equal(r.campos[1].texto, "Texto");
  assert.equal(r.campos[1].aprovada, true);
});

test("campos do edital: ordem e nomes dele, limite do edital, sem seção e seções restantes depois", () => {
  const criterios = {
    campos_exigidos: [{ nome: "Resumo da proposta", secao: "orcamento" }, { nome: "Termo de ciência", secao: null }, { nome: "Descrição", secao: "apresentacao" }, { nome: "Fantasma", secao: "inexistente" }],
    limites_caracteres: { orcamento: 900 },
  };
  const r = camposParaCopiar({ secoes, linhaDe, criterios });
  assert.equal(r.doEdital, true);
  assert.deepEqual(r.campos.map((c) => c.nome), ["Resumo da proposta", "Termo de ciência", "Descrição", "Fantasma", "Ficha do projeto"]);
  assert.equal(r.campos[0].limite, 900);
  assert.equal(r.campos[1].semSecao, true);
  assert.equal(r.campos[3].semSecao, true, "seção que não existe no assistente");
  assert.equal(r.campos[4].extra, true);
  assert.equal(r.campos[2].limite, 1500, "sem limite do edital, vale o padrão do spec");
});

test("lista vazia de campos do edital volta ao padrão", () => {
  assert.equal(camposParaCopiar({ secoes, linhaDe, criterios: { campos_exigidos: [] } }).doEdital, false);
});

test("tabela como texto: cabeçalho, números em vírgula, total calculado e sem tabulação dentro do texto", () => {
  const t = tabelaParaTexto("orcamento_itens", [{ etapa: "producao", rubrica: "Equipe", item: "Cachê\tdo\ndiretor", unidade: "mês", quantidade: 2, valor_unitario: 1500.5, fonte_preco: "x", justificativa: "y" }]);
  const [cab, linha] = t.split("\n");
  assert.equal(cab.split("\t").length, 9);
  const c = linha.split("\t");
  assert.deepEqual([c[0], c[2], c[4], c[5], c[6]], ["Produção", "Cachê do diretor", "2", "1500,5", "3001"]);
  assert.equal(tabelaParaTexto("cronograma_itens", [{ atividade: "Ensaios", etapa: "producao", mes_inicio: 1, mes_fim: 3 }]).split("\n")[1], "Ensaios\tProdução\t1\t3");
  assert.equal(tabelaParaTexto("tabela_que_nao_existe", []), "");
});

test("resumos do docx: orçamento por etapa com total e aviso, cronograma por período", () => {
  const orc = [
    { etapa: "producao", quantidade: 2, valor_unitario: 3000 },
    { etapa: "divulgacao", quantidade: 1, valor_unitario: 2000 },
    { etapa: "divulgacao", quantidade: 1, valor_unitario: null },
  ];
  const r = resumosParaDocx({ orcamento: orc, cronograma: [{ atividade: "Ensaios", etapa: "producao", mes_inicio: 1, mes_fim: 3 }, { atividade: "Estreia", etapa: "producao", mes_inicio: 4, mes_fim: 4 }] });
  assert.deepEqual(r.orcamento.cabecalho, ["Etapa", "Total", "% do total"]);
  assert.deepEqual(r.orcamento.linhas.map((l) => l[0]), ["Produção", "Divulgação", "Total geral"]);
  assert.match(r.orcamento.linhas[0][1], /R\$\s6\.000,00/);
  assert.equal(r.orcamento.linhas[0][2], "75%");
  assert.match(r.orcamento.aviso, /1 item\(ns\) ainda sem valor/);
  assert.deepEqual(r.cronograma.linhas.map((l) => l[2]), ["meses 1 a 3", "mês 4"]);
  assert.deepEqual(resumosParaDocx({}), { orcamento: null, cronograma: null });
});
