import assert from "node:assert/strict";
import { chaveProibida, fatosSeguros, fichaPermitida, MARCA, removerDadosPessoais } from "./privacidade.ts";

Deno.test("remove CPF, CNPJ, e-mail, telefone e CEP", () => {
  const casos: Array<[string, string]> = [
    ["Meu CPF é 123.456.789-09.", "cpf"],
    ["cpf 12345678909", "cpf"],
    ["CNPJ 12.345.678/0001-95 da associação", "cnpj"],
    ["escreva para ana.souza@exemplo.com.br", "email"],
    ["ligue (81) 99999-1234", "telefone"],
    ["whats +55 81 98888-7777", "telefone"],
    ["81 99999-1234", "telefone"],
    ["Rua das Flores, 10, CEP 50000-123", "cep"],
  ];
  for (const [texto, tipo] of casos) {
    const r = removerDadosPessoais(texto);
    assert.ok(r.texto.includes(MARCA), texto);
    assert.ok(r.removidos.includes(tipo), `${texto} → ${r.removidos}`);
    assert.ok(!/\d{3}\.\d{3}\.\d{3}-\d{2}|@exemplo|99999|98888|50000-123|12\.345\.678/.test(r.texto), r.texto);
  }
});

Deno.test("não mexe em valores, anos, datas e números do projeto", () => {
  for (const t of [
    "O projeto custa R$ 150.000,00 e acontece de 10/12/2026 a 20/12/2026.",
    "Atendemos 1.200 pessoas em 8 oficinas, entre 2024 e 2026.",
    "Meta: 3.500 ingressos em 12 meses; orçamento de R$ 1.250.000.",
    "Edital 003/2026, processo 2026.0001.1234",
  ]) {
    assert.equal(removerDadosPessoais(t).texto, t);
  }
});

Deno.test("ficha: tira chaves de dados pessoais e de cotas", () => {
  const ficha = {
    tipo_proponente: "MEI", cidades: ["Recife"], custo_estimado: 80000,
    cpf: "123.456.789-09", cnpj: "12.345.678/0001-95", nome_completo: "Ana Souza", email: "a@b.com",
    telefone: "(81) 99999-1234", endereco: "Rua X", cotas: ["pessoa negra"], cotas_interesse: "pcd",
    raca_cor: "parda", ideia: "Oficina de cordel. Contato: ana@exemplo.com",
  };
  const p = fichaPermitida(ficha);
  assert.deepEqual(Object.keys(p).sort(), ["cidades", "custo_estimado", "ideia", "tipo_proponente"]);
  assert.ok(!JSON.stringify(p).includes("ana@exemplo.com"));
});

Deno.test("cotas só entram se a própria pessoa marcar", () => {
  const f = { cotas: ["interior"], cotas_usar_no_texto: true, cidades: ["Sobral"] };
  assert.deepEqual(fichaPermitida(f), { cotas: ["interior"], cidades: ["Sobral"] });
  assert.deepEqual(fichaPermitida({ ...f, cotas_usar_no_texto: false }), { cidades: ["Sobral"] });
});

Deno.test("fatos do LLM: chaves estranhas e proibidas são descartadas", () => {
  const f = fatosSeguros({ linguagem: "Teatro", "Chave Ruim": "x", cpf: "1", telefone_contato: "1", cotas: "x", obs: "ligue 81 98888-7777" });
  assert.deepEqual(Object.keys(f).sort(), ["linguagem", "obs"]);
  assert.ok(!String(f.obs).includes("98888"));
  assert.equal(chaveProibida("tipo_proponente"), false);
  assert.equal(chaveProibida("cpf_responsavel"), true);
});
