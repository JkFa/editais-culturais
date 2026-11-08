import assert from "node:assert/strict";
import { criarHandler } from "./handler.ts";
import { FONTE_USUARIO, filtrarItensPropostos, numerosDoTexto, salvarItens } from "./itens.ts";
import { llmFalso, perguntaOk, projetoPadrao, repoFalso, specs } from "./_util_teste.ts";

const PID = projetoPadrao().id;
const U1 = "0f8fad5b-d9cb-469f-a165-70867728950e";
const U2 = "1a8fad5b-d9cb-469f-a165-70867728950e";

Deno.test("números que a pessoa escreveu: milhares, vírgula e 'mil'", () => {
  const n = numerosDoTexto(["O cachê é R$ 2.000 e o som custa 1.500,50. Impressão: 3 mil. Cenário 1,5 mil."]);
  for (const esperado of [2000, 1500.5, 3000, 1500]) assert.ok(n.includes(esperado), `faltou ${esperado}`);
});

Deno.test("PREÇO: o LLM nunca define preço; só vale número que a pessoa escreveu", () => {
  const r = filtrarItensPropostos(
    {
      tabela: "orcamento_itens",
      linhas: [
        { etapa: "producao", item: "Cachê do diretor", quantidade: 1, valor_unitario: 2000, fonte_preco: "tabela do sindicato", justificativa: "Direção" },
        { etapa: "producao", item: "Som", quantidade: 1, valor_unitario: 1800, fonte_preco: "orçamento de fornecedor", justificativa: "Áudio" },
        { etapa: "divulgacao", item: "Cartaz", quantidade: 100, valor_unitario: 3.5 },
      ],
    },
    { tabelaEsperada: "orcamento_itens", textosUsuario: ["O diretor recebe R$ 2.000, conforme a tabela do sindicato."] },
  );
  assert.equal(r.precosRemovidos, 2);
  const [diretor, som, cartaz] = r.itens!.linhas;
  assert.equal(diretor.valor_unitario, 2000);
  assert.equal(diretor.fonte_preco, "tabela do sindicato"); // a fonte foi dita pela pessoa
  assert.equal(som.valor_unitario, null); // 1800 não foi dito
  assert.equal(som.fonte_preco, null); // e a fonte inventada vai junto
  assert.equal(cartaz.valor_unitario, null);
});

Deno.test("PREÇO: número dito sem a fonte vira 'informado pelo usuário'", () => {
  const r = filtrarItensPropostos(
    { tabela: "orcamento_itens", linhas: [{ etapa: "producao", item: "Locação", quantidade: 1, valor_unitario: 900, fonte_preco: "preço de mercado em Recife" }] },
    { tabelaEsperada: "orcamento_itens", textosUsuario: ["A locação sai por 900."] },
  );
  assert.equal(r.itens!.linhas[0].fonte_preco, FONTE_USUARIO);
});

Deno.test("mídia: custo também só se a pessoa disse; ligação com o orçamento nasce vazia", () => {
  const r = filtrarItensPropostos(
    { tabela: "midia_itens", linhas: [{ peca: "Posts patrocinados", canal: "Instagram", custo: 400, orcamento_item_id: U1 }, { peca: "Cartaz", custo: 250 }] },
    { tabelaEsperada: "midia_itens", textosUsuario: ["Vou gastar 250 com cartazes."] },
  );
  assert.deepEqual(r.itens!.linhas.map((l) => [l.peca, l.custo, l.orcamento_item_id]), [["Posts patrocinados", null, null], ["Cartaz", 250, null]]);
});

Deno.test("equipe: o LLM não inventa nome; nome só se a pessoa escreveu; ligações vazias", () => {
  const r = filtrarItensPropostos(
    {
      tabela: "equipe_membros",
      linhas: [
        { funcao: "Direção", nome_artistico_ou_funcao: "Maria Inventada", mini_curriculo: "Dirige há 10 anos.", local: "Recife", orcamento_item_ids: [U1] },
        { funcao: "Produção", nome_artistico_ou_funcao: "Joana Silva", local: "Olinda" },
      ],
    },
    { tabelaEsperada: "equipe_membros", textosUsuario: ["A produção é da Joana Silva, de Olinda."] },
  );
  assert.deepEqual(r.itens!.linhas.map((l) => [l.nome_artistico_ou_funcao, l.orcamento_item_ids]), [["Direção", []], ["Joana Silva", []]]);
});

Deno.test("tabela errada, linhas inválidas e dados pessoais não passam", () => {
  const prop = { tabela: "cronograma_itens", linhas: [{ etapa: "producao", atividade: "Ensaios", mes_inicio: 1, mes_fim: 2 }] };
  assert.equal(filtrarItensPropostos(prop, { tabelaEsperada: "orcamento_itens", textosUsuario: [] }).itens, null);
  assert.equal(filtrarItensPropostos(null, { tabelaEsperada: "cronograma_itens", textosUsuario: [] }).itens, null);
  const r = filtrarItensPropostos(
    {
      tabela: "cronograma_itens",
      linhas: [
        { etapa: "producao", atividade: "Ligar para 81 99999-1234", mes_inicio: 1, mes_fim: 2 },
        { etapa: "producao", atividade: "Mês invertido", mes_inicio: 5, mes_fim: 2 },
        { etapa: "luxo", atividade: "Etapa inválida", mes_inicio: 1, mes_fim: 1 },
      ],
    },
    { tabelaEsperada: "cronograma_itens", textosUsuario: [] },
  );
  assert.equal(r.descartadas, 2);
  assert.ok(!JSON.stringify(r.itens).includes("99999"));
});

Deno.test("acessibilidade: tudo vai para a etapa acessibilidade", () => {
  const r = filtrarItensPropostos(
    { tabela: "orcamento_itens", linhas: [{ etapa: "producao", item: "Intérprete de Libras", quantidade: 4 }] },
    { tabelaEsperada: "orcamento_itens", etapaPadrao: "acessibilidade", textosUsuario: [] },
  );
  assert.equal(r.itens!.linhas[0].etapa, "acessibilidade");
});

// ── gravação ─────────────────────────────────────────────────────────────────────────────────

Deno.test("salvar: valida, gera ids, calcula o resumo e grava pelo repositório (service_role)", async () => {
  const t = repoFalso();
  const r = await salvarItens(t.repo, {
    projetoId: PID,
    tabela: "orcamento_itens",
    linhas: [
      { etapa: "producao", item: "Cachê", quantidade: "2", valor_unitario: "1.500,00", fonte_preco: "sindicato", projeto_id: "outro-projeto", user_id: "x" },
      { etapa: "divulgacao", item: "Cartaz", quantidade: 1, valor_unitario: null },
    ],
  });
  assert.ok(r.ok);
  if (!r.ok) return;
  assert.equal(r.resumo!.total, 3000);
  assert.equal(r.resumo!.semValor, 1);
  assert.equal(t.gravacoes.length, 1);
  const g = t.gravacoes[0];
  assert.equal(g.projetoId, PID);
  assert.ok(g.linhas.every((l) => typeof l.id === "string" && l.projeto_id === undefined && l.user_id === undefined), "campos estranhos descartados");
  assert.deepEqual(g.linhas.map((l) => l.ordem), [0, 1]);
  assert.equal(t.limpezas.length, 1); // equipe perde ligações com itens que sumiram
});

Deno.test("salvar: linha inválida bloqueia tudo e diz qual campo", async () => {
  const t = repoFalso();
  const r = await salvarItens(t.repo, { projetoId: PID, tabela: "orcamento_itens", linhas: [{ etapa: "producao", item: "ok" }, { etapa: "luxo", item: "", quantidade: -1 }] });
  assert.ok(!r.ok && r.codigo === "validacao");
  if (r.ok) return;
  assert.equal(r.erros![0].indice, 1);
  assert.deepEqual(Object.keys(r.erros![0].campos).sort(), ["etapa", "item", "quantidade"]);
  assert.equal(t.gravacoes.length, 0, "nada é gravado");
});

Deno.test("salvar: tabela desconhecida, lista enorme e id de OUTRO projeto são recusados", async () => {
  const t = repoFalso({ idsDeOutros: [U2] });
  assert.ok(!(await salvarItens(t.repo, { projetoId: PID, tabela: "projetos", linhas: [] })).ok);
  assert.ok(!(await salvarItens(t.repo, { projetoId: PID, tabela: "__proto__", linhas: [] })).ok);
  assert.ok(!(await salvarItens(t.repo, { projetoId: PID, tabela: "orcamento_itens", linhas: Array(301).fill({ etapa: "producao", item: "x" }) })).ok);
  const r = await salvarItens(t.repo, { projetoId: PID, tabela: "orcamento_itens", linhas: [{ id: U2, etapa: "producao", item: "x" }] });
  assert.ok(!r.ok && r.codigo === "id_conflito");
  const rep = await salvarItens(t.repo, { projetoId: PID, tabela: "orcamento_itens", linhas: [{ id: U1, etapa: "producao", item: "a" }, { id: U1, etapa: "producao", item: "b" }] });
  assert.ok(!rep.ok);
  assert.equal(t.gravacoes.length, 0);
});

Deno.test("salvar: lista vazia apaga as linhas do projeto (usuário esvaziou a tabela)", async () => {
  const t = repoFalso();
  const r = await salvarItens(t.repo, { projetoId: PID, tabela: "cronograma_itens", linhas: [] });
  assert.ok(r.ok);
  assert.deepEqual(t.gravacoes[0], { tabela: "cronograma_itens", projetoId: PID, linhas: [] });
});

Deno.test("salvar: equipe e mídia só se ligam a itens do orçamento DESTE projeto", async () => {
  const t = repoFalso({ idsOrcamento: [U1] });
  const eq = await salvarItens(t.repo, { projetoId: PID, tabela: "equipe_membros", linhas: [{ funcao: "Direção", orcamento_item_ids: [U1, U2] }] });
  assert.ok(eq.ok);
  assert.deepEqual(t.gravacoes[0].linhas[0].orcamento_item_ids, [U1]);
  const md = await salvarItens(t.repo, { projetoId: PID, tabela: "midia_itens", linhas: [{ peca: "Cartaz", orcamento_item_id: U2 }] });
  assert.ok(md.ok && md.avisos.length === 1);
  assert.equal(t.gravacoes[1].linhas[0].orcamento_item_id, null);
});

Deno.test("salvar: CPF, e-mail e telefone digitados na equipe são removidos", async () => {
  const t = repoFalso();
  const r = await salvarItens(t.repo, { projetoId: PID, tabela: "equipe_membros", linhas: [{ funcao: "Direção", mini_curriculo: "Contato ana@exemplo.com, CPF 123.456.789-09." }] });
  assert.ok(r.ok);
  const cur = String(t.gravacoes[0].linhas[0].mini_curriculo);
  assert.ok(!cur.includes("ana@exemplo.com") && !cur.includes("123.456.789-09"));
  assert.ok(r.ok && r.avisos.length === 1);
});

// ── pelo handler ─────────────────────────────────────────────────────────────────────────────

const chamar = (h: (r: Request) => Promise<Response>, corpo: unknown, cab: Record<string, string> = {}) =>
  h(new Request("https://x.supabase.co/functions/v1/assistente", {
    method: "POST",
    headers: { authorization: "Bearer jwt", "content-type": "application/json", origin: "https://jkfa.github.io", ...cab },
    body: JSON.stringify(corpo),
  }));
function montar(o: Parameters<typeof repoFalso>[0] = {}, llm: Parameters<typeof llmFalso>[0] = [perguntaOk]) {
  const r = repoFalso(o);
  const l = llmFalso(llm);
  const logs: Array<Record<string, unknown>> = [];
  const h = criarHandler({ repo: r.repo, provedor: () => l.provedor, specs, origens: ["https://jkfa.github.io"], limiteDiario: 60, log: (e) => logs.push(e) });
  return { h, ...r, ...l, logs };
}

Deno.test("handler salvar_itens: 200 com resumo, sem gastar o limite diário nem chamar a IA", async () => {
  const t = montar();
  const r = await chamar(t.h, { projeto_id: PID, acao: "salvar_itens", tabela: "orcamento_itens", linhas: [{ etapa: "producao", item: "Cachê", quantidade: 1, valor_unitario: 100, fonte_preco: "x" }] });
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.resumo.total, 100);
  assert.equal(t.consumos.length, 0);
  assert.equal(t.chamadas.length, 0);
  assert.ok(!JSON.stringify(t.logs).includes("Cachê"), "o log não leva texto do usuário");
});

Deno.test("handler salvar_itens: sem sessão 401; projeto alheio 404; inválido 422", async () => {
  const sem = montar({ usuario: null });
  assert.equal((await chamar(sem.h, { projeto_id: PID, acao: "salvar_itens", tabela: "orcamento_itens", linhas: [] })).status, 401);
  const alheio = montar({ projeto: null });
  assert.equal((await chamar(alheio.h, { projeto_id: PID, acao: "salvar_itens", tabela: "orcamento_itens", linhas: [] })).status, 404);
  assert.equal(alheio.gravacoes.length, 0);
  const t = montar();
  const r = await chamar(t.h, { projeto_id: PID, acao: "salvar_itens", tabela: "orcamento_itens", linhas: [{ etapa: "luxo" }] });
  assert.equal(r.status, 422);
  assert.equal((await r.json()).erros[0].indice, 0);
  assert.equal((await chamar(t.h, { projeto_id: "nao-uuid", acao: "salvar_itens", tabela: "orcamento_itens", linhas: [] })).status, 400);
});

Deno.test("handler: seção de orçamento devolve linhas propostas já filtradas e avisa dos preços tirados", async () => {
  const rascunho = {
    tipo: "rascunho",
    perguntas: [],
    rascunho: "Orçamento por etapa.",
    alertas: [],
    fatos_novos_ficha: [],
    itens_propostos: { tabela: "orcamento_itens", linhas: [{ etapa: "producao", item: "Cachê", quantidade: 1, valor_unitario: 2000, fonte_preco: "sindicato" }, { etapa: "producao", item: "Som", quantidade: 1, valor_unitario: 1800 }] },
  };
  const t = montar({ textosDoUsuario: ["O cachê é 2.000 conforme o sindicato."] }, [rascunho]);
  const r = await chamar(t.h, { projeto_id: PID, secao_id: "orcamento", acao: "redigir" });
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.deepEqual(j.itens_propostos.linhas.map((l: Record<string, unknown>) => [l.item, l.valor_unitario]), [["Cachê", 2000], ["Som", null]]);
  assert.ok(j.alertas.some((a: { texto: string }) => /1 valor/.test(a.texto)));
  assert.equal(t.gravacoes.length, 0, "propor não grava nada: quem confirma é o usuário");
  assert.equal(t.salvos[0].resposta.itens_propostos?.linhas.length, 2);
});

Deno.test("handler: seção de texto (sem tabela) ignora itens_propostos do LLM", async () => {
  const r0 = { tipo: "rascunho", perguntas: [], rascunho: "Texto.", alertas: [], fatos_novos_ficha: [], itens_propostos: { tabela: "orcamento_itens", linhas: [{ etapa: "producao", item: "x" }] } };
  const t = montar({}, [r0]);
  const j = await (await chamar(t.h, { projeto_id: PID, secao_id: "metas", acao: "redigir" })).json();
  assert.equal(j.itens_propostos, null);
});

Deno.test("handler: critérios do edital alimentam as perguntas condicionais da seção", async () => {
  const t = montar({ criterios: { exige_acessibilidade: true, contrapartida_exigida: null, revisado: false } }, [perguntaOk]);
  await chamar(t.h, { projeto_id: PID, secao_id: "acessibilidade", acao: "responder" });
  assert.match(t.chamadas[0].prompt, /exigencia_do_edital_acessibilidade/);
  assert.match(t.chamadas[0].prompt, /lidos automaticamente/);
  const sem = montar({}, [perguntaOk]);
  await chamar(sem.h, { projeto_id: PID, secao_id: "acessibilidade", acao: "responder" });
  assert.ok(!/exigencia_do_edital_acessibilidade/.test(sem.chamadas[0].prompt));
});

Deno.test("LGPD: nomes e currículos da equipe nunca vão no prompt", async () => {
  const t = montar({ tabelas: { equipe_membros: [{ id: U1, funcao: "Direção", nome_artistico_ou_funcao: "Fulana de Tal", mini_curriculo: "Formada em 2010", local: "Recife", orcamento_item_ids: [] }] } }, [perguntaOk]);
  await chamar(t.h, { projeto_id: PID, secao_id: "orcamento", acao: "responder" });
  const p = t.chamadas[0].prompt;
  assert.match(p, /"funcao": "Direção"/);
  assert.ok(!p.includes("Fulana de Tal") && !p.includes("Formada em 2010"));
});
