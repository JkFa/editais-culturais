import assert from "node:assert/strict";
import { test } from "node:test";
import { avaliarCondicao, pendentes, perguntasAplicaveis, secoesDoFluxo } from "./secoes.mjs";

const ctx = { ficha: { territorio_prioritario: true, n: 3, cidade: "Recife", vazio: null } };

test("condições simples e compostas", () => {
  assert.equal(avaliarCondicao("ficha.territorio_prioritario == true", ctx), true);
  assert.equal(avaliarCondicao("ficha.territorio_prioritario == false", ctx), false);
  assert.equal(avaliarCondicao("ficha.n == 3", ctx), true);
  assert.equal(avaliarCondicao('ficha.cidade == "Recife"', ctx), true);
  assert.equal(avaliarCondicao('ficha.cidade != "Natal"', ctx), true);
  assert.equal(avaliarCondicao("ficha.ausente", ctx), false);
  assert.equal(avaliarCondicao("ficha.ausente == null", ctx), true);
  assert.equal(avaliarCondicao("ficha.ausente || ficha.n == 3", ctx), true);
  assert.equal(avaliarCondicao("ficha.n == 3 && ficha.ausente", ctx), false);
});

test("entrada maliciosa não executa código", () => {
  assert.throws(() => avaliarCondicao("ficha.n == process.exit(1)", ctx));
  assert.throws(() => avaliarCondicao("constructor.constructor('return 1')()", ctx));
  assert.equal(avaliarCondicao("ficha.__proto__.x", ctx), false);
});

const spec = {
  perguntas: [
    { id: "a", texto: "pergunta a", obrigatoria: true },
    { id: "b", texto: "pergunta b", obrigatoria: false },
    { id: "f", texto: "pergunta f", canal: "formulario" },
  ],
  condicionais: [{ se: "ficha.territorio_prioritario == true", pergunta: { id: "c", texto: "pergunta c" } }],
};

test("perguntas aplicáveis respeitam condicionais e canal", () => {
  assert.deepEqual(perguntasAplicaveis(spec, ctx).map((p) => p.id), ["a", "b", "f", "c"]);
  assert.deepEqual(perguntasAplicaveis(spec, { ficha: {} }).map((p) => p.id), ["a", "b", "f"]);
  assert.deepEqual(perguntasAplicaveis(spec, ctx, { canal: "chat" }).map((p) => p.id), ["a", "b", "c"]);
});

test("pendentes: só obrigatórias do chat sem resposta", () => {
  assert.deepEqual(pendentes(spec, ctx, new Set()).map((p) => p.id), ["a", "c"]);
  assert.deepEqual(pendentes(spec, ctx, new Set(["a"])).map((p) => p.id), ["c"]);
});

test("seções do fluxo: ativas do fluxo + 'em breve'", () => {
  const todas = [
    { id: "x", ordem: 1, ativo: true, fluxos: ["padrao"] },
    { id: "y", ordem: 0, ativo: true, fluxos: ["audiovisual"] },
    { id: "z", ordem: 2, ativo: false },
  ];
  assert.deepEqual(secoesDoFluxo(todas, "padrao").map((s) => s.id), ["x", "z"]);
});
