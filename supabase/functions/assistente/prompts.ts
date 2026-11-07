// Templates dos prompts como constantes testáveis. Um prompt de sistema fixo, um bloco de
// contexto (ficha + seções aprovadas + critérios) e um bloco da seção. Texto original do projeto.
import { fichaPermitida, removerDadosPessoais } from "./privacidade.ts";

export type Pergunta = { id: string; texto: string; obrigatoria?: boolean; canal?: string };
export type SpecSecao = {
  id: string;
  titulo: string;
  perguntas: Pergunta[];
  condicionais?: Array<{ se: string; pergunta: Pergunta }>;
  checagens: string[];
  limite_caracteres_padrao: number;
  instrucoes_redacao: string;
};
export type Acao = "responder" | "redigir" | "reabrir";
export type Criterios = {
  criterios?: unknown[];
  teto_orcamento?: number | null;
  linguagens?: string[];
  campos_exigidos?: Record<string, unknown>;
  limites_caracteres?: Record<string, number>;
} | null;

export type EntradaPrompt = {
  acao: Acao;
  spec: SpecSecao;
  /** Perguntas do chat que valem agora (fixas + condicionais verdadeiras). */
  aplicaveis: Pergunta[];
  /** Ids das obrigatórias ainda sem resposta. */
  pendentes: string[];
  ficha: Record<string, unknown>;
  edital: { titulo?: string | null };
  criterios: Criterios;
  aprovadas: Array<{ id: string; titulo: string; rascunho: string }>;
  respostas: Array<{ pergunta_id: string; resposta: string }>;
  historico: Array<{ papel: "usuario" | "assistente"; texto: string }>;
  mensagem?: string;
  rascunhoAtual?: string;
  limite: number;
  checagensCatalogo: Record<string, string>;
  /** Quantas perguntas de aprofundamento já foram feitas sobre cada pergunta. */
  aprofundamentos: Record<string, number>;
};

export const PROMPT_SISTEMA = `Você é o Assistente de Projetos Culturais de um portal gratuito de editais. Ajuda pessoas a escrever o projeto que vão inscrever em um edital, uma seção por vez, em português do Brasil, com linguagem simples e respeitosa.

REGRAS QUE VALEM SEMPRE
1. Use SOMENTE fatos que estão na ficha, nas respostas e nas seções aprovadas abaixo. Nunca invente nomes, números, datas, locais, parcerias, prêmios, trajetórias ou dados de pesquisa.
2. Onde faltar informação para escrever, escreva [COMPLETAR: descrição do que falta] no lugar. Não preencha lacunas com suposições.
3. Nunca peça nem repita dados pessoais (nome completo, CPF, CNPJ, RG, endereço, telefone, e-mail, datas de nascimento). Se a pessoa os enviar, ignore-os e diga que ficam no formulário do proponente.
4. Nunca prometa aprovação nem diga que o projeto "vai ganhar". O edital oficial é a referência.
5. Texto de projeto: tom técnico-cultural, terceira pessoa, frases curtas, sem adjetivos vazios ("incrível", "único", "inovador") a não ser que haja justificativa nos fatos.
6. Responda SEMPRE em JSON no formato pedido, sem texto fora do JSON.

FORMATO DA RESPOSTA (JSON)
- "tipo": "pergunta" quando ainda falta informação e você vai conversar; "rascunho" quando você entrega o texto da seção.
- "perguntas": lista de { "id", "texto" }. Preencha só quando tipo = "pergunta" (no máximo 2). Para perguntas do roteiro use o id exato do roteiro. Para aprofundamento use o id "<id_da_pergunta>:aprofundar".
- "rascunho": o texto da seção. Preencha só quando tipo = "rascunho"; caso contrário, string vazia.
- "alertas": lista de { "nivel": "info" | "aviso", "texto" } com as checagens da seção que falharam ou dúvidas úteis. Pode ser vazia.
- "fatos_novos_ficha": lista de pares { "chave", "valor" } com fatos objetivos novos que a pessoa acabou de dar (chaves em minúsculas_com_sublinhado). Sem dados pessoais. Pode ser vazia.`;

export const TAREFA_RESPONDER = `TAREFA: conduzir a conversa desta seção.
1. Faça as perguntas obrigatórias que ainda não foram respondidas, 1 ou 2 por vez, em tom de conversa. Use o texto do roteiro, adaptando-o ao que a pessoa já contou.
2. Avalie a última resposta da pessoa: completa, vaga ou com dado faltando. Se estiver vaga, faça no máximo 2 perguntas de aprofundamento sobre aquele ponto (veja "aprofundamentos já feitos") e depois siga em frente.
3. Considere as perguntas condicionais já listadas no roteiro.
4. Se NÃO houver obrigatórias pendentes, entregue tipo = "rascunho" agora.`;

export const TAREFA_REDIGIR = `TAREFA: entregar o rascunho desta seção agora (tipo = "rascunho"), mesmo que falte informação. Onde faltar, escreva [COMPLETAR: ...]. Se houver critérios do edital, espelhe o vocabulário deles apenas quando for verdade para o projeto. Respeite o limite de caracteres. Liste em "alertas" as checagens da seção que falharem. Se a pessoa deixou uma instrução na mensagem, siga-a sem inventar fatos.`;

export const TAREFA_REABRIR = `TAREFA: a pessoa reabriu esta seção para melhorar o texto. Leia o rascunho atual e faça no máximo 2 perguntas (tipo = "pergunta") sobre o que ela quer mudar ou complementar, apontando lacunas reais do texto atual. Não reescreva o texto agora.`;

const TAREFAS: Record<Acao, string> = { responder: TAREFA_RESPONDER, redigir: TAREFA_REDIGIR, reabrir: TAREFA_REABRIR };

/** JSON com chaves ordenadas: prompts previsíveis (snapshots) e sem ruído. */
export function jsonEstavel(v: unknown): string {
  const ordenar = (x: unknown): unknown =>
    Array.isArray(x)
      ? x.map(ordenar)
      : x && typeof x === "object"
      ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => a.localeCompare(b)).map(([k, y]) => [k, ordenar(y)]))
      : x;
  return JSON.stringify(ordenar(v), null, 2);
}

export function blocoContexto(e: EntradaPrompt): string {
  const partes = ["## CONTEXTO DO PROJETO", "### Ficha (fatos já informados)", jsonEstavel(fichaPermitida(e.ficha))];
  partes.push("### Edital", e.edital.titulo ? `Título: ${e.edital.titulo}` : "Projeto avulso (sem edital vinculado).");
  const c = e.criterios;
  if (c && (c.criterios?.length || c.teto_orcamento || Object.keys(c.limites_caracteres ?? {}).length)) {
    partes.push("### Critérios do edital (use o vocabulário só se for verdade para o projeto)", jsonEstavel(c));
  } else {
    partes.push("### Critérios do edital", "Não disponíveis. Use as orientações padrão.");
  }
  partes.push("### Seções já aprovadas (consistência)");
  if (e.aprovadas.length === 0) partes.push("Nenhuma ainda.");
  for (const a of e.aprovadas) partes.push(`#### ${a.titulo}`, a.rascunho);
  return partes.join("\n");
}

export function blocoSecao(e: EntradaPrompt): string {
  const respondidas = new Map<string, string[]>();
  for (const r of e.respostas) {
    for (const id of r.pergunta_id.split("+")) respondidas.set(id, [...(respondidas.get(id) ?? []), r.resposta]);
  }
  const linhas = [`## SEÇÃO: ${e.spec.titulo} (id: ${e.spec.id})`, `Orientação desta seção: ${e.spec.instrucoes_redacao}`, `Limite de caracteres do texto: ${e.limite}`];
  linhas.push("### Roteiro de perguntas");
  for (const p of e.aplicaveis) {
    const obrig = p.obrigatoria === false ? "opcional" : "obrigatória";
    const estado = e.pendentes.includes(p.id) ? "PENDENTE" : respondidas.has(p.id) ? "respondida" : "não pendente";
    linhas.push(`- [${p.id}] (${obrig}, ${estado}) ${p.texto}`);
  }
  linhas.push("### Respostas da pessoa até agora");
  if (e.respostas.length === 0) linhas.push("Nenhuma ainda.");
  for (const r of e.respostas) linhas.push(`- [${r.pergunta_id}] ${r.resposta}`);
  linhas.push("### Aprofundamentos já feitos (máximo 2 por pergunta)", jsonEstavel(e.aprofundamentos));
  linhas.push("### Checagens desta seção");
  for (const c of e.spec.checagens) linhas.push(`- ${e.checagensCatalogo[c] ?? c}`);
  if (e.historico.length) {
    linhas.push("### Conversa recente");
    for (const m of e.historico) linhas.push(`${m.papel === "usuario" ? "Pessoa" : "Assistente"}: ${m.texto}`);
  }
  if (e.rascunhoAtual) linhas.push("### Rascunho atual da seção", e.rascunhoAtual);
  if (e.mensagem) linhas.push("### Mensagem mais recente da pessoa", e.mensagem);
  linhas.push("", TAREFAS[e.acao]);
  return linhas.join("\n");
}

/** Monta sistema + usuário. O filtro final de dados pessoais é a rede de segurança. */
export function montarPrompt(e: EntradaPrompt): { sistema: string; usuario: string } {
  const usuario = `${blocoContexto(e)}\n\n${blocoSecao(e)}`;
  return { sistema: PROMPT_SISTEMA, usuario: removerDadosPessoais(usuario).texto };
}
