import assert from "node:assert/strict";
import { test } from "node:test";
import { executarChecagens, objetivosEspecificos, ordenarPendencias, temLigacao } from "./checagens.mjs";

const specs = [
  { id: "objetivos", titulo: "Objetivos", limite_caracteres_padrao: 2000 },
  { id: "metas", titulo: "Metas", limite_caracteres_padrao: 2500 },
  { id: "acessibilidade", titulo: "Acessibilidade", limite_caracteres_padrao: 2000 },
  { id: "orcamento", titulo: "Orçamento", limite_caracteres_padrao: 2500 },
];
const regras = (r) => r.map((p) => p.regra);
const por = (r, regra) => r.filter((p) => p.regra === regra);
const rodar = (e) => executarChecagens({ specs, secoes: {}, orcamento: [], cronograma: [], midia: [], equipe: [], criterios: null, ficha: {}, ...e });

test("extrai objetivos específicos (ignora o geral) e metas de listas numeradas", () => {
  const t = "Objetivo geral: fortalecer o teatro.\nObjetivos específicos:\n1. Realizar oficinas de teatro em escolas\n2) Montar um espetáculo infantil\n- Apresentar o espetáculo na biblioteca";
  assert.deepEqual(objetivosEspecificos(t), [
    "Realizar oficinas de teatro em escolas",
    "Montar um espetáculo infantil",
    "Apresentar o espetáculo na biblioteca",
  ]);
});

test("objetivo sem meta correspondente", () => {
  const r = rodar({
    secoes: {
      objetivos: { rascunho: "1. Realizar oficinas de teatro nas escolas\n2. Publicar um livro de contos ilustrados" },
      metas: { rascunho: "Meta 1: 8 oficinas de teatro nas escolas até o mês 4, comprovadas por lista de presença" },
    },
  });
  const p = por(r, "objetivo_sem_meta");
  assert.equal(p.length, 1);
  assert.match(p[0].mensagem, /O objetivo "Publicar um livro de contos ilustrados" não tem meta correspondente/);
  assert.equal(p[0].secao, "metas");
});

test("classificação por IA, quando existe, substitui a heurística; sem metas escritas, 'não verificado'", () => {
  const r = rodar({
    secoes: { objetivos: { rascunho: "1. Realizar oficinas de teatro nas escolas" }, metas: { rascunho: "Meta 1: oficinas de teatro nas escolas" } },
    ligacoes: { objetivos: [{ texto: "Realizar oficinas de teatro nas escolas", tem_meta: false }] },
  });
  assert.equal(por(r, "objetivo_sem_meta").length, 1);
  const sem = rodar({ secoes: { objetivos: { rascunho: "1. Realizar oficinas de teatro nas escolas" } } });
  assert.equal(por(sem, "objetivo_sem_meta")[0].nivel, "nao_verificado");
});

test("meta sem atividade no cronograma", () => {
  const r = rodar({
    secoes: { metas: { rascunho: "Meta 1: 8 oficinas de teatro nas escolas\nMeta 2: lançamento do livro com 300 exemplares" } },
    cronograma: [
      { atividade: "Oficinas de teatro nas escolas", etapa: "producao", mes_inicio: 1, mes_fim: 4 },
      { atividade: "Prestação de contas", etapa: "administrativo", mes_inicio: 6, mes_fim: 6 },
    ],
  });
  const p = por(r, "meta_sem_atividade");
  assert.equal(p.length, 1);
  assert.match(p[0].mensagem, /A meta "Meta 2: lançamento do livro com 300 exemplares" não aparece no cronograma|A meta "lançamento do livro/);
});

test("cronograma: prazo do edital e prestação de contas no fim", () => {
  const cron = [
    { atividade: "Ensaios", etapa: "producao", mes_inicio: 1, mes_fim: 8 },
    { atividade: "Prestação de contas", etapa: "administrativo", mes_inicio: 5, mes_fim: 5 },
  ];
  const r = rodar({ cronograma: cron, criterios: { prazo_execucao_meses: 6 } });
  assert.equal(por(r, "cronograma_acima_do_prazo")[0].nivel, "erro");
  assert.equal(por(r, "prestacao_nao_e_a_ultima").length, 1);
  assert.equal(por(rodar({ cronograma: [cron[0]] }), "cronograma_sem_prestacao").length, 1);
});

test("total acima do teto, percentual acima do limite e rubrica vedada", () => {
  const orc = [
    { id: "a", etapa: "producao", rubrica: "Equipe", item: "Direção", quantidade: 1, valor_unitario: 7000, fonte_preco: "x", justificativa: "y" },
    { id: "b", etapa: "divulgacao", rubrica: "Divulgação", item: "Anúncio", quantidade: 1, valor_unitario: 2000, fonte_preco: "x", justificativa: "y" },
    { id: "c", etapa: "administrativo", rubrica: "Bebida alcoólica", item: "Coquetel", quantidade: 1, valor_unitario: 1000, fonte_preco: "x", justificativa: "y" },
  ];
  const r = rodar({ orcamento: orc, criterios: { teto_orcamento: 9000, percentuais_max: { divulgacao: 0.15 }, rubricas_vedadas: ["bebida alcoólica"] } });
  assert.match(por(r, "total_acima_do_teto")[0].mensagem, /R\$ 10\.000,00.*R\$ 9\.000,00.*R\$ 1\.000,00/);
  assert.match(por(r, "etapa_acima_do_limite")[0].mensagem, /Divulgação é 20%.*limita a 15%/);
  assert.equal(por(r, "rubrica_vedada")[0].itemId, "c");
  assert.equal(r.filter((p) => p.nivel === "erro").length, 3);
  // dentro dos limites: nada disso aparece
  const ok = rodar({ orcamento: orc.slice(0, 2), criterios: { teto_orcamento: 20000, percentuais_max: { divulgacao: 0.5 } } });
  assert.deepEqual(regras(ok).filter((x) => ["total_acima_do_teto", "etapa_acima_do_limite", "rubrica_vedada"].includes(x)), []);
});

test("teto cai para o da ficha quando o edital não tem critérios", () => {
  const r = rodar({ orcamento: [{ id: "a", etapa: "producao", item: "x", quantidade: 1, valor_unitario: 5000, fonte_preco: "f", justificativa: "j" }], ficha: { edital_teto_por_projeto: 4000 } });
  assert.equal(por(r, "total_acima_do_teto").length, 1);
});

test("itens sem fonte, sem justificativa e com valor a confirmar", () => {
  const r = rodar({ orcamento: [{ id: "a", etapa: "producao", item: "x", quantidade: 1, valor_unitario: null }, { id: "b", etapa: "producao", item: "y", quantidade: 1, valor_unitario: 10, fonte_preco: "f" }] });
  assert.match(por(r, "item_sem_fonte")[0].mensagem, /^1 item/);
  assert.match(por(r, "item_sem_justificativa")[0].mensagem, /^2 item/);
  assert.match(por(r, "valor_a_confirmar")[0].mensagem, /^1 item/);
});

test("total do orçamento diferente do valor declarado na ficha", () => {
  const orc = [{ id: "a", etapa: "producao", item: "x", quantidade: 1, valor_unitario: 8000, fonte_preco: "f", justificativa: "j" }];
  assert.equal(por(rodar({ orcamento: orc, ficha: { custo_estimado: 10000 } }), "total_diferente_da_ficha").length, 1);
  assert.equal(por(rodar({ orcamento: orc, ficha: { custo_estimado: 8040 } }), "total_diferente_da_ficha").length, 0); // <1%
});

test("medida de acessibilidade citada sem custo", () => {
  const secoes = { acessibilidade: { rascunho: "Haverá intérprete de Libras em todas as sessões e audiodescrição na estreia." } };
  const sem = rodar({ secoes });
  assert.deepEqual(por(sem, "acessibilidade_sem_custo").map((p) => p.mensagem.match(/"([^"]+)"/)[1]), ["intérprete de Libras", "audiodescrição"]);
  const com = rodar({ secoes, orcamento: [{ id: "a", etapa: "acessibilidade", item: "Intérprete de Libras", quantidade: 4, valor_unitario: 300 }] });
  assert.deepEqual(por(com, "acessibilidade_sem_custo").map((p) => p.mensagem.match(/"([^"]+)"/)[1]), ["audiodescrição"]);
});

test("percentual mínimo de acessibilidade exigido pelo edital", () => {
  const orc = [
    { id: "a", etapa: "producao", item: "x", quantidade: 1, valor_unitario: 9800 },
    { id: "b", etapa: "acessibilidade", item: "Libras", quantidade: 1, valor_unitario: 200 },
  ];
  const r = rodar({ orcamento: orc, criterios: { percentuais_min: { acessibilidade: 0.05 } } });
  assert.match(por(r, "etapa_abaixo_do_minimo")[0].mensagem, /Acessibilidade é 2%.*no mínimo 5%/);
});

test("peça paga sem custo no orçamento", () => {
  const orc = [{ id: "o1", etapa: "divulgacao", item: "Impulsionamento", quantidade: 1, valor_unitario: 500 }];
  const r = rodar({
    orcamento: orc,
    midia: [
      { id: "m1", peca: "Posts patrocinados", canal: "Instagram (patrocinado)", custo: null },
      { id: "m2", peca: "Cartaz", canal: "Rua", custo: 800 },
      { id: "m3", peca: "Vídeo", canal: "YouTube", custo: 500 }, // igual a um item de divulgação do orçamento
      { id: "m4", peca: "Release", canal: "Imprensa", custo: 0 },
    ],
  });
  assert.deepEqual(por(r, "peca_paga_sem_custo").map((p) => p.itemId), ["m1", "m2"]);
});

test("função paga sem pessoa na equipe (e pessoa sem cachê)", () => {
  const orc = [
    { id: "o1", etapa: "producao", rubrica: "Equipe", item: "Cachê da direção", quantidade: 1, valor_unitario: 3000 },
    { id: "o2", etapa: "producao", rubrica: "Equipe", item: "Cachê da cenografia", quantidade: 1, valor_unitario: 2000 },
    { id: "o3", etapa: "producao", rubrica: "Materiais", item: "Tecidos", quantidade: 1, valor_unitario: 400 },
  ];
  const equipe = [
    { id: "e1", funcao: "Direção", nome_artistico_ou_funcao: "Diretora", orcamento_item_ids: ["o1"] },
    { id: "e2", funcao: "Produção", nome_artistico_ou_funcao: "", orcamento_item_ids: [] },
  ];
  const r = rodar({ orcamento: orc, equipe });
  assert.deepEqual(por(r, "funcao_paga_sem_pessoa").map((p) => p.itemId), ["o2"]);
  assert.deepEqual(por(r, "pessoa_sem_cache").map((p) => p.itemId), ["e2"]);
  assert.equal(por(r, "pessoa_sem_cache")[0].nivel, "info");
});

test("seção acima do limite de caracteres (do edital ou do padrão) e [COMPLETAR] restantes", () => {
  const secoes = { objetivos: { rascunho: "x".repeat(300) + " [COMPLETAR: a] [COMPLETAR: b]" }, metas: { rascunho: "y".repeat(120) } };
  const r = rodar({ secoes, criterios: { limites_caracteres: { objetivos: 200, metas: 100 } } });
  const lim = por(r, "secao_acima_do_limite");
  assert.deepEqual(lim.map((p) => p.secao), ["objetivos", "metas"]);
  assert.match(lim[0].mensagem, /tem 330 caracteres e o limite é 200/);
  assert.match(por(r, "completar_pendente")[0].mensagem, /Objetivos: 2 ponto\(s\)/);
  // sem critérios, vale o padrão do spec (2000)
  assert.equal(por(rodar({ secoes }), "secao_acima_do_limite").length, 0);
});

test("contrapartida exigida pelo edital precisa aparecer na seção de democratização", () => {
  const c = { contrapartida_exigida: "Realizar duas oficinas gratuitas em escolas públicas" };
  assert.equal(por(rodar({ criterios: c, secoes: { democratizacao: { rascunho: "Haverá sessões extras para a comunidade." } } }), "contrapartida_nao_citada").length, 1);
  assert.equal(por(rodar({ criterios: c, secoes: { democratizacao: { rascunho: "Duas oficinas gratuitas em escolas públicas." } } }), "contrapartida_nao_citada").length, 0);
});

test("ordenação: erros primeiro, 'não verificado' por último; projeto vazio não acusa nada", () => {
  const lista = [{ nivel: "info" }, { nivel: "nao_verificado" }, { nivel: "erro" }, { nivel: "aviso" }];
  assert.deepEqual(ordenarPendencias(lista).map((p) => p.nivel), ["erro", "aviso", "info", "nao_verificado"]);
  assert.deepEqual(rodar({}), []);
});

test("temLigacao não afirma falta quando não há palavra de conteúdo", () => {
  assert.equal(temLigacao("Realizar 3 atividades", ["qualquer coisa"]), true);
  assert.equal(temLigacao("oficinas de teatro", ["Oficina de teatro nas escolas"]), true);
  assert.equal(temLigacao("publicação de livro", ["oficina de teatro"]), false);
});
