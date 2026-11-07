import assert from "node:assert/strict";
import { blocoContexto, jsonEstavel, montarPrompt, PROMPT_SISTEMA, TAREFA_REABRIR, TAREFA_REDIGIR, TAREFA_RESPONDER } from "./prompts.ts";
import { entradaBase, PII, spec } from "./_util_teste.ts";

const DIR = new URL("./__snapshots__/", import.meta.url);

function conferir(nome: string, texto: string) {
  const arquivo = new URL(`${nome}.txt`, DIR);
  if (Deno.env.get("UPDATE_SNAPSHOTS")) {
    Deno.mkdirSync(DIR, { recursive: true });
    Deno.writeTextFileSync(arquivo, texto);
    return;
  }
  assert.equal(texto, Deno.readTextFileSync(arquivo), `snapshot ${nome} mudou (UPDATE_SNAPSHOTS=1 para atualizar, depois revise o diff)`);
}

const mensagemComPii = `Meu CPF é ${PII[0]}, e-mail ${PII[2]}, tel ${PII[3]}, CEP ${PII[4]}. Quero fazer oficinas.`;
const aprovada = { id: "apresentacao", titulo: "Apresentação / Resumo", rascunho: `Oficina em Recife. Responsável: CNPJ ${PII[1]}.` };

const cenarios = {
  "responder-ficha": entradaBase("ficha", { mensagem: mensagemComPii, historico: [{ papel: "usuario", texto: mensagemComPii }] }),
  "redigir-justificativa": entradaBase("justificativa", {
    acao: "redigir",
    aprovadas: [aprovada],
    respostas: [{ pergunta_id: "necessidade", resposta: `Falta teatro. Fale comigo: ${PII[2]}` }],
    pendentes: ["por_que_voce", "relevancia", "dados"],
    mensagem: "deixe mais curto",
    edital: { titulo: `Edital do CPF ${PII[0]}` },
  }),
  "reabrir-objetivos": entradaBase("objetivos", { acao: "reabrir", rascunhoAtual: `1. Formar plateia. Tel ${PII[3]}` }),
};

for (const [nome, entrada] of Object.entries(cenarios)) {
  Deno.test(`snapshot: ${nome}`, () => {
    const { sistema, usuario } = montarPrompt(entrada);
    conferir(nome, `${sistema}\n\n=====\n\n${usuario}\n`);
  });

  Deno.test(`LGPD: nenhum dado pessoal em ${nome}`, () => {
    const { sistema, usuario } = montarPrompt(entrada);
    for (const x of PII) {
      assert.ok(!sistema.includes(x) && !usuario.includes(x), `vazou: ${x}`);
    }
    assert.ok(!/"?(cpf|cnpj|telefone|email|endereco|nome_completo|cotas)"?\s*:/.test(usuario), "chave de dado pessoal na ficha");
  });
}

Deno.test("cotas só entram no prompt se a pessoa pediu", () => {
  const e = entradaBase("ficha");
  const com = montarPrompt({ ...e, ficha: { ...e.ficha, cotas_usar_no_texto: true } }).usuario;
  assert.ok(com.includes("pessoa negra"), "com autorização, as cotas entram");
  assert.ok(!montarPrompt(e).usuario.includes("pessoa negra"));
});

Deno.test("templates: regras do roteiro estão nas constantes", () => {
  assert.match(PROMPT_SISTEMA, /Nunca invente/);
  assert.match(PROMPT_SISTEMA, /\[COMPLETAR: descrição do que falta\]/);
  assert.match(PROMPT_SISTEMA, /terceira pessoa/);
  assert.match(TAREFA_RESPONDER, /1 ou 2 por vez/);
  assert.match(TAREFA_RESPONDER, /no máximo 2 perguntas de aprofundamento/);
  assert.match(TAREFA_REDIGIR, /\[COMPLETAR/);
  assert.match(TAREFA_REABRIR, /no máximo 2 perguntas/);
});

Deno.test("sem critérios do edital, o prompt diz que não há", () => {
  assert.match(blocoContexto(entradaBase("ficha")), /Não disponíveis/);
  const c = blocoContexto(entradaBase("ficha", { criterios: { criterios: ["Relevância cultural"], teto_orcamento: 100000 } }));
  assert.match(c, /Relevância cultural/);
});

Deno.test("perguntas de formulário e condicionais falsas não aparecem", () => {
  const sem = montarPrompt(entradaBase("justificativa", { aplicaveis: spec("justificativa").perguntas })).usuario;
  assert.ok(!sem.includes("impacto_territorio"));
  const { usuario } = montarPrompt(entradaBase("ficha"));
  assert.ok(!usuario.includes("[edital]") && !usuario.includes("[cotas]"));
});

Deno.test("jsonEstavel ordena as chaves", () => {
  assert.equal(jsonEstavel({ b: 1, a: { d: 1, c: 2 } }), jsonEstavel({ a: { c: 2, d: 1 }, b: 1 }));
});
