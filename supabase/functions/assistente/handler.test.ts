import assert from "node:assert/strict";
import { criarHandler, MSG_LIMITE, origemPermitida } from "./handler.ts";
import type { Deps } from "./handler.ts";
import { ErroLlm } from "./llm.ts";
import { ErroBanco } from "./repositorio.ts";
import { llmFalso, perguntaOk, PII, projetoPadrao, rascunhoOk, repoFalso, specs } from "./_util_teste.ts";

const ORIGENS = ["https://jkfa.github.io"];
const PID = projetoPadrao().id;

function montar(o: Parameters<typeof repoFalso>[0] = {}, llm: Parameters<typeof llmFalso>[0] = [perguntaOk], limiteDiario = 60) {
  const r = repoFalso(o);
  const l = llmFalso(llm);
  const logs: Array<Record<string, unknown>> = [];
  const deps: Deps = { repo: r.repo, provedor: () => l.provedor, specs, origens: ORIGENS, limiteDiario, log: (e) => logs.push(e) };
  return { handler: criarHandler(deps), ...r, ...l, logs };
}

const chamar = (h: (r: Request) => Promise<Response>, corpo: unknown, cab: Record<string, string> = {}) =>
  h(new Request("https://x.supabase.co/functions/v1/assistente", {
    method: "POST",
    headers: { authorization: "Bearer jwt-do-usuario", "content-type": "application/json", ...cab },
    body: typeof corpo === "string" ? corpo : JSON.stringify(corpo),
  }));
const base = { projeto_id: PID, secao_id: "ficha", acao: "responder" };

Deno.test("sem token: 401, e nada é consumido", async () => {
  const t = montar();
  const r = await t.handler(new Request("https://x/", { method: "POST", body: JSON.stringify(base) }));
  assert.equal(r.status, 401);
  assert.equal(t.consumos.length, 0);
});

Deno.test("sessão inválida: 401", async () => {
  const t = montar({ usuario: null });
  assert.equal((await chamar(t.handler, base)).status, 401);
  assert.equal(t.chamadas.length, 0);
});

Deno.test("projeto de outro usuário (RLS devolve nada): 404 sem consumir uso nem chamar a IA", async () => {
  const t = montar({ projeto: null });
  const r = await chamar(t.handler, base);
  assert.equal(r.status, 404);
  assert.equal(t.consumos.length, 0);
  assert.equal(t.chamadas.length, 0);
  assert.equal(t.salvos.length, 0);
});

Deno.test("projeto com user_id diferente do token: 404 (defesa em profundidade)", async () => {
  const t = montar({ projeto: projetoPadrao({ user_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb" }) });
  assert.equal((await chamar(t.handler, base)).status, 404);
});

Deno.test("entradas inválidas: 400", async () => {
  const t = montar();
  for (const corpo of [
    "não é json", {}, { ...base, projeto_id: "abc" }, { ...base, secao_id: "../x" }, { ...base, acao: "apagar" },
    { ...base, mensagem: 123 }, { ...base, mensagem: "x".repeat(4001) },
  ]) {
    assert.equal((await chamar(t.handler, corpo)).status, 400, JSON.stringify(corpo).slice(0, 60));
  }
  assert.equal(t.consumos.length, 0);
});

Deno.test("seção 'em breve' ou inexistente: 400", async () => {
  const t = montar();
  assert.equal((await chamar(t.handler, { ...base, secao_id: "inexistente" })).status, 400);
  // uma seção marcada como inativa continua bloqueada (as 12 seções estão ativas desde a Fase 2)
  const emBreve = { ...specs, secoes: specs.secoes.map((x) => (x.id === "equipe" ? { id: "equipe", titulo: "Equipe", ordem: 9, ativo: false } : x)) };
  const r = repoFalso();
  const h = criarHandler({ repo: r.repo, provedor: () => llmFalso([perguntaOk]).provedor, specs: emBreve, origens: ORIGENS, limiteDiario: 60, log: () => {} });
  assert.equal((await chamar(h, { ...base, secao_id: "equipe" })).status, 400);
});

Deno.test("limite diário: 429 amigável e a IA não é chamada", async () => {
  const t = montar({ usados: -1 }, [perguntaOk], 7);
  const r = await chamar(t.handler, base);
  assert.equal(r.status, 429);
  const j = await r.json();
  assert.equal(j.erro, "limite_diario");
  assert.equal(j.mensagem, MSG_LIMITE(7));
  assert.match(j.mensagem, /volte amanhã/);
  assert.equal(t.chamadas.length, 0);
  assert.deepEqual(t.consumos, [7]);
});

Deno.test("primeira rodada: pergunta, persistência e uso", async () => {
  const t = montar({ usados: 3 });
  const r = await chamar(t.handler, base);
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.tipo, "pergunta");
  assert.deepEqual(j.uso, { usados: 3, limite: 60 });
  assert.equal(t.salvos.length, 1);
  assert.equal(t.salvos[0].mensagemUsuario, undefined);
  assert.equal(t.salvos[0].resposta.tipo, "pergunta");
});

Deno.test("dados pessoais na mensagem: tirados antes da IA e do banco, com aviso", async () => {
  const t = montar();
  const r = await chamar(t.handler, { ...base, mensagem: `Sou MEI, CPF ${PII[0]}, e-mail ${PII[2]}, fone ${PII[3]}.` });
  const j = await r.json();
  for (const x of [PII[0], PII[2], PII[3]]) {
    assert.ok(!t.chamadas[0].prompt.includes(x), `vazou para a IA: ${x}`);
    assert.ok(!JSON.stringify(t.salvos[0]).includes(x), `vazou para o banco: ${x}`);
  }
  assert.ok(j.alertas.some((a: { nivel: string; texto: string }) => a.nivel === "aviso" && /dados pessoais/.test(a.texto)));
});

Deno.test("a resposta é atribuída às perguntas feitas por último (duas viram 'a+b')", async () => {
  const t = montar({
    contexto: { historico: [{ papel: "assistente", conteudo: { perguntas: [{ id: "tipo_proponente" }, { id: "linguagem" }] } }] },
  });
  await chamar(t.handler, { ...base, mensagem: "Sou MEI e faço teatro" });
  assert.deepEqual(t.salvos[0].respostas, [{ pergunta_id: "tipo_proponente+linguagem", resposta: "Sou MEI e faço teatro" }]);
  assert.match(t.chamadas[0].prompt, /\[tipo_proponente\+linguagem\] Sou MEI e faço teatro/);
  assert.match(t.chamadas[0].prompt, /\[tipo_proponente\] \(obrigatória, respondida\)/);
  assert.match(t.chamadas[0].prompt, /\[cidades\] \(obrigatória, PENDENTE\)/);
});

Deno.test("aprofundamentos já feitos vão para o prompt", async () => {
  const t = montar({ contexto: { respostas: [{ pergunta_id: "ideia", resposta: "x" }, { pergunta_id: "ideia:aprofundar", resposta: "y" }] } });
  await chamar(t.handler, base);
  assert.match(t.chamadas[0].prompt, /"ideia": 1/);
});

Deno.test("JSON inválido: tenta de novo uma vez e depois entrega", async () => {
  const t = montar({}, ["isto não é json", perguntaOk]);
  assert.equal((await chamar(t.handler, base)).status, 200);
  assert.equal(t.chamadas.length, 2);
  assert.match(t.chamadas[1].prompt, /resposta anterior era inválida/);
});

Deno.test("JSON inválido duas vezes: 502 tratado e nada é salvo", async () => {
  const t = montar({}, ["lixo", { tipo: "pergunta", perguntas: [] }]);
  const r = await chamar(t.handler, base);
  assert.equal(r.status, 502);
  assert.equal((await r.json()).erro, "resposta_invalida");
  assert.equal(t.chamadas.length, 2);
  assert.equal(t.salvos.length, 0);
});

Deno.test("erros do provedor viram status e mensagens amigáveis", async () => {
  for (const [tipo, status] of [["cota", 503], ["timeout", 504], ["config", 500], ["indisponivel", 502], ["bloqueado", 502]] as const) {
    const t = montar({}, [new ErroLlm(tipo, "detalhe técnico secreto")]);
    const r = await chamar(t.handler, base);
    assert.equal(r.status, status, tipo);
    const txt = await r.text();
    assert.ok(!txt.includes("detalhe técnico secreto"), "não expor detalhes internos");
    assert.equal(t.salvos.length, 0);
  }
});

Deno.test("falha do banco: 500 sem vazar detalhes", async () => {
  const t = montar();
  t.repo.salvarTurno = () => Promise.reject(new ErroBanco("tabela x HTTP 500"));
  const r = await chamar(t.handler, base);
  assert.equal(r.status, 500);
  assert.ok(!(await r.text()).includes("tabela x"));
});

Deno.test("redigir: salva rascunho, avisa [COMPLETAR] e limite de caracteres", async () => {
  const longo = `Texto. [COMPLETAR: público estimado] ${"a".repeat(1600)}`;
  const t = montar({}, [{ ...rascunhoOk, rascunho: longo }]);
  const j = await (await chamar(t.handler, { projeto_id: PID, secao_id: "apresentacao", acao: "redigir" })).json();
  assert.equal(j.tipo, "rascunho");
  assert.equal(t.salvos[0].resposta.rascunho, longo.trim());
  const textos = j.alertas.map((a: { texto: string }) => a.texto).join("|");
  assert.match(textos, /limite é 1500/);
  assert.match(textos, /1 ponto\(s\) marcado\(s\) com \[COMPLETAR\]/);
  assert.equal(j.limite_caracteres, 1500);
  assert.match(t.chamadas[0].prompt, /TAREFA: entregar o rascunho/);
});

Deno.test("redigir: a mensagem é instrução, não resposta do roteiro", async () => {
  const t = montar({}, [rascunhoOk]);
  await chamar(t.handler, { projeto_id: PID, secao_id: "apresentacao", acao: "redigir", mensagem: "mais curto" });
  assert.deepEqual(t.salvos[0].respostas, []);
  assert.match(t.chamadas[0].prompt, /Mensagem mais recente da pessoa\nmais curto/);
});

Deno.test("critérios do edital mudam o limite e o prompt", async () => {
  const t = montar({ criterios: { criterios: ["Relevância"], limites_caracteres: { apresentacao: 800 } } }, [rascunhoOk]);
  const j = await (await chamar(t.handler, { projeto_id: PID, secao_id: "apresentacao", acao: "redigir" })).json();
  assert.equal(j.limite_caracteres, 800);
  assert.match(t.chamadas[0].prompt, /Limite de caracteres do texto: 800/);
  assert.match(t.chamadas[0].prompt, /Relevância/);
});

Deno.test("ficha: custo acima do teto do edital gera aviso", async () => {
  const t = montar({ criterios: { teto_orcamento: 50000 } }, [{ ...rascunhoOk, fatos_novos_ficha: [{ chave: "custo_estimado", valor: "80000" }] }]);
  const j = await (await chamar(t.handler, base)).json();
  assert.ok(j.alertas.some((a: { texto: string }) => /passa do teto/.test(a.texto)));
});

Deno.test("ficha: sem critérios, o teto por projeto do edital (gravado na ficha) vale", async () => {
  const t = montar({ projeto: projetoPadrao({ ficha: { edital_teto_por_projeto: 50000 } }) }, [{ ...rascunhoOk, fatos_novos_ficha: [{ chave: "custo_estimado", valor: "80000" }] }]);
  const j = await (await chamar(t.handler, base)).json();
  assert.ok(j.alertas.some((a: { texto: string }) => /passa do teto.*50\.000/.test(a.texto)));
  const t2 = montar({ projeto: projetoPadrao({ ficha: { edital_teto_por_projeto: 100000 } }) }, [{ ...rascunhoOk, fatos_novos_ficha: [{ chave: "custo_estimado", valor: "80000" }] }]);
  const j2 = await (await chamar(t2.handler, base)).json();
  assert.ok(!j2.alertas.some((a: { texto: string }) => /teto/.test(a.texto)));
});

Deno.test("seções aprovadas entram como contexto; dados pessoais dentro delas são tirados", async () => {
  const t = montar({ contexto: { aprovadas: [{ secao_id: "apresentacao", rascunho: `Oficina. CNPJ ${PII[1]}.` }] } }, [rascunhoOk]);
  await chamar(t.handler, { projeto_id: PID, secao_id: "justificativa", acao: "redigir" });
  assert.match(t.chamadas[0].prompt, /#### Apresentação \/ Resumo\nOficina\./);
  assert.ok(!t.chamadas[0].prompt.includes(PII[1]));
});

Deno.test("fatos novos: viram ficha, mas chaves de dados pessoais são descartadas", async () => {
  const t = montar({}, [{ ...perguntaOk, fatos_novos_ficha: [{ chave: "linguagem", valor: "Teatro" }, { chave: "cpf", valor: "123" }, { chave: "cotas", valor: "x" }] }]);
  await chamar(t.handler, base);
  assert.deepEqual(t.salvos[0].ficha, { tipo_proponente: "MEI", cidades: ["Recife"], linguagem: "Teatro" });
});

Deno.test("ficha do projeto com dados pessoais nunca vai ao prompt", async () => {
  const t = montar({ projeto: projetoPadrao({ ficha: { cpf: PII[0], email: PII[2], cotas: [PII[5]], linguagem: "Teatro" } }) });
  await chamar(t.handler, base);
  for (const x of [PII[0], PII[2], PII[5]]) assert.ok(!t.chamadas[0].prompt.includes(x), x);
  assert.match(t.chamadas[0].prompt, /"linguagem": "Teatro"/);
});

Deno.test("reabrir marca o turno e usa o rascunho atual", async () => {
  const t = montar({ contexto: { secao: { rascunho: "Texto antigo da seção.", aprovado: true } } }, [perguntaOk]);
  await chamar(t.handler, { projeto_id: PID, secao_id: "apresentacao", acao: "reabrir" });
  assert.equal(t.salvos[0].reabrir, true);
  assert.match(t.chamadas[0].prompt, /Texto antigo da seção\./);
  assert.match(t.chamadas[0].prompt, /reabriu esta seção/);
});

Deno.test("fluxo audiovisual não recebe seção só do fluxo padrão (todas as 0-5 servem aos dois)", async () => {
  const t = montar({ projeto: projetoPadrao({ fluxo: "audiovisual" }) });
  assert.equal((await chamar(t.handler, base)).status, 200);
});

Deno.test("CORS: origem do Pages e localhost passam; outras não", async () => {
  assert.equal(origemPermitida("https://jkfa.github.io", ORIGENS), "https://jkfa.github.io");
  assert.equal(origemPermitida("http://localhost:8000", ORIGENS), "http://localhost:8000");
  assert.equal(origemPermitida("http://127.0.0.1:3000", ORIGENS), "http://127.0.0.1:3000");
  assert.equal(origemPermitida("https://malicioso.example", ORIGENS), null);
  assert.equal(origemPermitida("http://localhost.evil.com", ORIGENS), null);
  assert.equal(origemPermitida(null, ORIGENS), null);

  const t = montar();
  const ok = await chamar(t.handler, base, { origin: "https://jkfa.github.io" });
  assert.equal(ok.headers.get("access-control-allow-origin"), "https://jkfa.github.io");
  const ruim = await chamar(t.handler, base, { origin: "https://malicioso.example" });
  assert.equal(ruim.status, 403);
  assert.equal(ruim.headers.get("access-control-allow-origin"), null);
  const pre = await t.handler(new Request("https://x/", { method: "OPTIONS", headers: { origin: "https://jkfa.github.io" } }));
  assert.equal(pre.status, 204);
  assert.match(pre.headers.get("access-control-allow-headers") ?? "", /authorization/);
  assert.equal((await t.handler(new Request("https://x/", { method: "GET" }))).status, 405);
});

Deno.test("logs não carregam texto, mensagem, ficha nem identificadores", async () => {
  const t = montar();
  await chamar(t.handler, { ...base, mensagem: `Meu e-mail é ${PII[2]} e quero uma oficina de teatro` });
  const logs = JSON.stringify(t.logs);
  assert.ok(logs.length > 0);
  for (const proibido of [PII[2], "oficina", "Teatro", PID, projetoPadrao().user_id, "jwt-do-usuario"]) {
    assert.ok(!logs.includes(proibido), `log vazou: ${proibido}`);
  }
});
