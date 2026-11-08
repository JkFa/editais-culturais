import assert from "node:assert/strict";
import { test } from "node:test";
import { deFormulario, evidenciasLegiveis, linkDoDocumento, marcaDeRevisao, paraFormulario, patchSemCriterios } from "../../web/assets/js/admin/criterios-modelo.mjs";

const registro = {
  edital_id: 7, fonte_url: "https://exemplo.test/edital.pdf",
  teto_orcamento: 30000, teto_por_proponente: null,
  percentuais_max: { divulgacao: 0.15 }, percentuais_min: { acessibilidade: 0.05 },
  rubricas_vedadas: ["Bebidas alcoólicas", "Brindes"],
  limites_caracteres: { metas: 2500 },
  campos_exigidos: [{ nome: "Plano de trabalho", secao: null }, { nome: "Planilha", secao: "orcamento" }],
  contrapartida_exigida: "Duas oficinas gratuitas", prazo_execucao_meses: 12, exige_acessibilidade: true,
  observacoes: null,
  criterios: [{ id: "merito", nome: "Mérito cultural", descricao: "Qualidade", peso: 0.6, nota_max: 10, trecho_edital: "O mérito vale até 10 pontos", pagina: 12 }],
  evidencias: { teto_orcamento: { trecho: "valor máximo de R$ 30.000,00", pagina: 2 }, "percentuais_max.divulgacao": { trecho: "divulgação até 15%", pagina: 5 }, "campos_exigidos.1": { trecho: "planilha orçamentária", pagina: null } },
};

test("registro do banco vira formulário em texto (percentuais em %, vírgula decimal)", () => {
  const f = paraFormulario(registro);
  assert.equal(f.teto_orcamento, "30000");
  assert.equal(f.percentuais_max.divulgacao, "15");
  assert.equal(f.percentuais_min.acessibilidade, "5");
  assert.equal(f.percentuais_max.producao, "");
  assert.equal(f.rubricas_vedadas, "Bebidas alcoólicas\nBrindes");
  assert.equal(f.limites_caracteres.metas, "2500");
  assert.equal(f.exige_acessibilidade, "sim");
  assert.equal(paraFormulario({ ...registro, exige_acessibilidade: false }).exige_acessibilidade, "nao");
  assert.equal(paraFormulario({ ...registro, exige_acessibilidade: null }).exige_acessibilidade, "");
  assert.equal(f.criterios[0].peso, "0,6");
});

test("ida e volta sem perder nada", () => {
  const { patch, erros } = deFormulario(paraFormulario(registro));
  assert.equal(erros, undefined);
  assert.equal(patch.teto_orcamento, 30000);
  assert.deepEqual(patch.percentuais_max, { divulgacao: 0.15 });
  assert.deepEqual(patch.percentuais_min, { acessibilidade: 0.05 });
  assert.deepEqual(patch.rubricas_vedadas, ["Bebidas alcoólicas", "Brindes"]);
  assert.deepEqual(patch.limites_caracteres, { metas: 2500 });
  assert.deepEqual(patch.campos_exigidos, registro.campos_exigidos);
  assert.equal(patch.exige_acessibilidade, true);
  assert.equal(patch.criterios[0].peso, 0.6);
  assert.equal(patch.criterios[0].trecho_edital, "O mérito vale até 10 pontos", "o trecho do edital é preservado");
});

test("valores com vírgula e milhar do jeito que a pessoa digita", () => {
  const f = paraFormulario(registro);
  f.teto_orcamento = "R$ 45.500,50";
  f.percentuais_max.divulgacao = "12,5";
  const { patch } = deFormulario(f);
  assert.equal(patch.teto_orcamento, 45500.5);
  assert.equal(patch.percentuais_max.divulgacao, 0.125);
});

test("erros: teto zero, percentual fora de 0-100, limite pequeno, prazo fracionado, critério sem nome", () => {
  const f = paraFormulario(registro);
  f.teto_orcamento = "0";
  f.percentuais_max.divulgacao = "150";
  f.limites_caracteres.metas = "50";
  f.prazo_execucao_meses = "2,5";
  f.criterios[0].nome = "x";
  f.criterios[0].peso = "abc";
  const { erros, patch } = deFormulario(f);
  assert.equal(patch, undefined);
  assert.deepEqual(Object.keys(erros).sort(), ["criterios.0.nome", "criterios.0.peso", "limites.metas", "percentuais_max.divulgacao", "prazo_execucao_meses", "teto_orcamento"]);
});

test("campos vazios viram nulo/vazio e linha de campo em branco some", () => {
  const f = paraFormulario({});
  f.campos_exigidos.push({ nome: "  ", secao: "" }, { nome: "Item", secao: "" });
  const { patch } = deFormulario(f);
  assert.equal(patch.teto_orcamento, null);
  assert.deepEqual(patch.percentuais_max, {});
  assert.equal(patch.exige_acessibilidade, null);
  assert.deepEqual(patch.campos_exigidos, [{ nome: "Item", secao: null }]);
  assert.equal(patch.contrapartida_exigida, null);
});

test("sem critérios aproveitáveis zera tudo e marcação de revisão leva quem e quando", () => {
  const p = patchSemCriterios();
  assert.deepEqual([p.criterios, p.percentuais_max, p.rubricas_vedadas, p.teto_orcamento, p.exige_acessibilidade], [[], {}, [], null, null]);
  assert.match(p.observacoes, /não traz critérios aproveitáveis/);
  const m = marcaDeRevisao("u-1", new Date("2026-11-08T12:00:00Z"));
  assert.deepEqual(m, { revisado: true, revisado_em: "2026-11-08T12:00:00.000Z", revisado_por: "u-1" });
});

test("link para a página do documento: PDF ganha #page=N, HTML e URLs com âncora não", () => {
  assert.equal(linkDoDocumento("https://x.test/e.pdf", 12), "https://x.test/e.pdf#page=12");
  assert.equal(linkDoDocumento("https://x.test/e.pdf", null), "https://x.test/e.pdf");
  assert.equal(linkDoDocumento("https://x.test/pagina", 3), "https://x.test/pagina");
  assert.equal(linkDoDocumento("https://x.test/e.pdf#a", 3), "https://x.test/e.pdf#a");
  assert.equal(linkDoDocumento(null, 3), null);
});

test("evidências legíveis juntam campos e critérios", () => {
  const ev = evidenciasLegiveis(registro);
  assert.deepEqual(ev.map((e) => e.rotulo), ["Teto por projeto", "% máximo (divulgacao)", "Campo exigido nº 2", "Critério: Mérito cultural"]);
  assert.equal(ev[0].pagina, 2);
  assert.equal(ev[3].pagina, 12);
});
