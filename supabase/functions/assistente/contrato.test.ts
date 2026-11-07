import assert from "node:assert/strict";
import { ErroContrato, lerJsonDoModelo, SCHEMA_RESPOSTA, validarResposta } from "./contrato.ts";

const ok = { tipo: "pergunta", perguntas: [{ id: "o_que", texto: "O que será feito?" }], rascunho: "", alertas: [], fatos_novos_ficha: [] };

Deno.test("aceita pergunta e rascunho válidos", () => {
  const p = validarResposta(ok);
  assert.equal(p.tipo, "pergunta");
  assert.equal(p.perguntas.length, 1);
  const r = validarResposta({ tipo: "rascunho", perguntas: [], rascunho: "  Texto. ", alertas: [{ nivel: "aviso", texto: "Falta X" }], fatos_novos_ficha: {} });
  assert.equal(r.rascunho, "Texto.");
  assert.deepEqual(r.alertas, [{ nivel: "aviso", texto: "Falta X" }]);
});

Deno.test("fatos como lista de pares viram objeto e passam pelo filtro de privacidade", () => {
  const r = validarResposta({ ...ok, fatos_novos_ficha: [{ chave: "linguagem", valor: "Música" }, { chave: "cpf", valor: "1" }, { chave: "custo_estimado", valor: "5000" }] });
  assert.deepEqual(r.fatos_novos_ficha, { linguagem: "Música", custo_estimado: "5000" });
});

Deno.test("limita a 2 perguntas e normaliza ids", () => {
  const r = validarResposta({ ...ok, perguntas: [1, 2, 3].map((i) => ({ id: `Pergunta ${i}!`, texto: `Texto ${i}` })) });
  assert.equal(r.perguntas.length, 2);
  assert.equal(r.perguntas[0].id, "pergunta_1_");
});

Deno.test("rejeita respostas inválidas", () => {
  const invalidas: unknown[] = [
    null, "texto", [], {},
    { ...ok, tipo: "outro" },
    { ...ok, perguntas: [] },
    { ...ok, perguntas: [{ id: "a" }] },
    { ...ok, perguntas: [{ id: "a", texto: "   " }] },
    { tipo: "rascunho", perguntas: [], rascunho: "  ", alertas: [], fatos_novos_ficha: [] },
    { ...ok, alertas: [{ nivel: "erro", texto: "x" }] },
    { ...ok, alertas: ["x"] },
    { ...ok, fatos_novos_ficha: "texto" },
  ];
  for (const x of invalidas) assert.throws(() => validarResposta(x), ErroContrato, JSON.stringify(x));
});

Deno.test("lê JSON com cerca de markdown e rejeita lixo", () => {
  assert.deepEqual(lerJsonDoModelo('```json\n{"a":1}\n```'), { a: 1 });
  assert.throws(() => lerJsonDoModelo("Claro! Aqui está: {"), ErroContrato);
  assert.throws(() => lerJsonDoModelo(""), ErroContrato);
});

Deno.test("o esquema do provedor exige todos os campos do contrato", () => {
  assert.deepEqual([...SCHEMA_RESPOSTA.required].sort(), ["alertas", "fatos_novos_ficha", "perguntas", "rascunho", "tipo"]);
});
