// Fluxo da Edge Function `assistente`. Todas as dependências entram por `Deps`, para testar sem rede.
import { ErroContrato, lerJsonDoModelo, SCHEMA_RESPOSTA, validarResposta } from "./contrato.ts";
import type { Alerta, RespostaAssistente } from "./contrato.ts";
import { ErroLlm } from "./llm.ts";
import type { ProvedorLlm } from "./llm.ts";
import { montarPrompt } from "./prompts.ts";
import type { Acao, SpecSecao } from "./prompts.ts";
import { removerDadosPessoais } from "./privacidade.ts";
import { ErroBanco } from "./repositorio.ts";
import type { Repositorio } from "./repositorio.ts";
// @ts-ignore: módulo JS compartilhado (gerado por scripts/sincronizar_specs.mjs)
import { pendentes as calcularPendentes, perguntasAplicaveis } from "./secoes.mjs";

export type Specs = { secoes: Array<Record<string, unknown>>; checagens: Record<string, string> };
export type Deps = {
  repo: Repositorio;
  provedor: () => ProvedorLlm;
  specs: Specs;
  origens: string[];
  limiteDiario: number;
  log: (evento: Record<string, unknown>) => void;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ACOES = new Set<Acao>(["responder", "redigir", "reabrir"]);
const MAX_MENSAGEM = 4000;
const MAX_RASCUNHO_APROVADO = 3500;

export const MSG_LIMITE = (limite: number) =>
  `Você chegou ao limite de ${limite} interações de hoje com o assistente. Seu trabalho está salvo; volte amanhã para continuar.`;

export function origemPermitida(origem: string | null, permitidas: string[]): string | null {
  if (!origem) return null;
  if (permitidas.includes(origem)) return origem;
  return /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origem) ? origem : null;
}

function resposta(corpo: unknown, status: number, cors: Record<string, string>): Response {
  return new Response(JSON.stringify(corpo), { status, headers: { ...cors, "content-type": "application/json; charset=utf-8" } });
}
const erro = (status: number, codigo: string, mensagem: string, cors: Record<string, string>) =>
  resposta({ erro: codigo, mensagem }, status, cors);

export function criarHandler(d: Deps) {
  return async function (req: Request): Promise<Response> {
    const inicio = Date.now();
    const origem = req.headers.get("origin");
    const permitida = origemPermitida(origem, d.origens);
    const cors: Record<string, string> = permitida ? { "Access-Control-Allow-Origin": permitida, Vary: "Origin" } : { Vary: "Origin" };

    if (req.method === "OPTIONS") {
      return new Response(null, {
        status: permitida ? 204 : 403,
        headers: { ...cors, "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info" },
      });
    }
    if (req.method !== "POST") return erro(405, "metodo", "Use POST.", cors);
    if (origem && !permitida) return erro(403, "origem", "Origem não permitida.", cors);

    const token = /^Bearer\s+(.+)$/i.exec(req.headers.get("authorization") ?? "")?.[1];
    if (!token) return erro(401, "nao_autenticado", "Entre na sua conta para usar o assistente.", cors);

    let entrada: { projeto_id?: unknown; secao_id?: unknown; acao?: unknown; mensagem?: unknown };
    try {
      entrada = await req.json();
    } catch {
      return erro(400, "json", "Corpo da requisição inválido.", cors);
    }
    const { projeto_id, secao_id, acao } = entrada;
    if (typeof projeto_id !== "string" || !UUID.test(projeto_id)) return erro(400, "projeto_id", "Projeto inválido.", cors);
    if (typeof secao_id !== "string" || !/^[a-z_]{2,40}$/.test(secao_id)) return erro(400, "secao_id", "Seção inválida.", cors);
    if (typeof acao !== "string" || !ACOES.has(acao as Acao)) return erro(400, "acao", "Ação inválida.", cors);
    if (entrada.mensagem != null && (typeof entrada.mensagem !== "string" || entrada.mensagem.length > MAX_MENSAGEM)) {
      return erro(400, "mensagem", `A mensagem deve ter até ${MAX_MENSAGEM} caracteres.`, cors);
    }
    const acaoOk = acao as Acao;
    const log = (extra: Record<string, unknown>) =>
      d.log({ evento: "assistente", acao: acaoOk, secao_id, ms: Date.now() - inicio, ...extra }); // sem texto e sem identificadores

    try {
      // 1. JWT e dono do projeto (a leitura usa o JWT do usuário: a RLS filtra).
      const userId = await d.repo.usuarioDoToken(token);
      if (!userId) return erro(401, "sessao_invalida", "Sua sessão expirou. Entre de novo.", cors);
      const projeto = await d.repo.carregarProjeto(token, projeto_id);
      if (!projeto || projeto.user_id !== userId) {
        log({ status: 404 });
        return erro(404, "projeto_nao_encontrado", "Projeto não encontrado.", cors);
      }
      const spec = d.specs.secoes.find((s) => s.id === secao_id) as (SpecSecao & { ativo?: boolean; fluxos?: string[]; depende_de?: string[] }) | undefined;
      if (!spec || spec.ativo === false || !(spec.fluxos ?? []).includes(projeto.fluxo)) {
        return erro(400, "secao_indisponivel", "Esta seção ainda não está disponível.", cors);
      }

      // 2. Limite diário.
      const usados = await d.repo.consumirUso(userId, d.limiteDiario);
      if (usados < 0) {
        log({ status: 429 });
        return erro(429, "limite_diario", MSG_LIMITE(d.limiteDiario), cors);
      }

      // 3. Contexto (sem dados pessoais).
      const [ctx, criterios] = await Promise.all([
        d.repo.carregarSecao(token, projeto_id, secao_id, spec.depende_de ?? []),
        d.repo.carregarCriterios(token, projeto.edital_id),
      ]);
      const alertasServidor: Alerta[] = [];
      let mensagem: string | undefined;
      if (typeof entrada.mensagem === "string" && entrada.mensagem.trim()) {
        const lim = removerDadosPessoais(entrada.mensagem.trim());
        mensagem = lim.texto;
        if (lim.removidos.length) {
          alertasServidor.push({ nivel: "aviso", texto: "Tirei dados pessoais (CPF, CNPJ, e-mail, telefone ou CEP) da sua mensagem. Eles ficam só no formulário do proponente e não vão para a IA." });
        }
      }

      // 4. A mensagem responde às perguntas feitas por último (só no modo "responder").
      const novasRespostas: Array<{ pergunta_id: string; resposta: string }> = [];
      if (acaoOk === "responder" && mensagem) {
        const ultima = [...ctx.historico].reverse().find((m) => m.papel === "assistente");
        const ids = ((ultima?.conteudo?.perguntas as Array<{ id?: string }> | undefined) ?? []).map((p) => p.id).filter((x): x is string => typeof x === "string");
        novasRespostas.push({ pergunta_id: ids.length ? ids.join("+") : "livre", resposta: mensagem });
      }
      const todasRespostas = [...ctx.respostas, ...novasRespostas];
      const respondidas = new Set(todasRespostas.flatMap((r) => r.pergunta_id.split("+")));
      const contexto = { ficha: projeto.ficha ?? {} };
      const aplicaveis = perguntasAplicaveis(spec, contexto, { canal: "chat" });
      const pend: string[] = calcularPendentes(spec, contexto, respondidas).map((p: { id: string }) => p.id);
      const aprofundamentos: Record<string, number> = {};
      for (const r of todasRespostas) {
        const m = /^(.+):aprofundar/.exec(r.pergunta_id);
        if (m) aprofundamentos[m[1]] = (aprofundamentos[m[1]] ?? 0) + 1;
      }
      const limite = Math.max(200, Number(criterios?.limites_caracteres?.[spec.id]) || spec.limite_caracteres_padrao);
      const limpar = (t: string) => removerDadosPessoais(t).texto;

      const entradaPrompt = {
        acao: acaoOk,
        spec,
        aplicaveis,
        pendentes: pend,
        ficha: projeto.ficha ?? {},
        edital: { titulo: projeto.edital_titulo ? limpar(projeto.edital_titulo) : null },
        criterios,
        aprovadas: ctx.aprovadas.map((a) => ({
          id: a.secao_id,
          titulo: (d.specs.secoes.find((s) => s.id === a.secao_id)?.titulo as string) ?? a.secao_id,
          rascunho: limpar(a.rascunho).slice(0, MAX_RASCUNHO_APROVADO),
        })),
        respostas: todasRespostas,
        historico: ctx.historico.slice(-6).map((m) => ({
          papel: m.papel,
          texto: limpar(m.papel === "usuario" ? String(m.conteudo?.texto ?? "") : ((m.conteudo?.perguntas as Array<{ texto: string }> | undefined) ?? []).map((p) => p.texto).join(" ") || String(m.conteudo?.rascunho ?? "").slice(0, 300)),
        })),
        mensagem: acaoOk === "responder" ? undefined : mensagem, // no "responder" a mensagem já está nas respostas
        rascunhoAtual: ctx.secao?.rascunho ? limpar(ctx.secao.rascunho) : undefined,
        limite,
        checagensCatalogo: d.specs.checagens,
        aprofundamentos,
      };
      const { sistema, usuario } = montarPrompt(entradaPrompt);

      // 5–6. LLM com JSON estruturado; se vier inválido, tenta mais uma vez.
      const provedor = d.provedor();
      let saida: RespostaAssistente | null = null;
      let motivo = "";
      for (let tentativa = 0; tentativa < 2 && !saida; tentativa++) {
        const prompt = tentativa === 0 ? usuario : `${usuario}\n\nSua resposta anterior era inválida (${motivo}). Responda de novo SOMENTE com o JSON no formato pedido.`;
        const bruto = await provedor.gerar(prompt, { sistema, schema: SCHEMA_RESPOSTA as unknown as Record<string, unknown>, temperatura: 0.4 });
        try {
          saida = validarResposta(lerJsonDoModelo(bruto.texto));
        } catch (e) {
          if (!(e instanceof ErroContrato)) throw e;
          motivo = e.message;
        }
      }
      if (!saida) {
        log({ status: 502, motivo: "resposta_invalida" });
        return erro(502, "resposta_invalida", "A IA devolveu uma resposta que não consegui entender. Tente de novo.", cors);
      }

      // Checagens determinísticas do servidor.
      if (saida.tipo === "rascunho") {
        if (saida.rascunho.length > limite) {
          alertasServidor.push({ nivel: "aviso", texto: `O texto tem ${saida.rascunho.length} caracteres e o limite é ${limite}. Corte ${saida.rascunho.length - limite}.` });
        }
        const faltas = (saida.rascunho.match(/\[COMPLETAR:/g) ?? []).length;
        if (faltas) alertasServidor.push({ nivel: "info", texto: `Há ${faltas} ponto(s) marcado(s) com [COMPLETAR]. Preencha antes de aprovar.` });
      }
      const teto = Number(criterios?.teto_orcamento);
      const custo = Number((saida.fatos_novos_ficha.custo_estimado ?? projeto.ficha?.custo_estimado) as unknown);
      if (spec.id === "ficha" && teto > 0 && custo > teto) {
        alertasServidor.push({ nivel: "aviso", texto: `O custo estimado passa do teto do edital (R$ ${teto.toLocaleString("pt-BR")}).` });
      }
      saida.alertas = [...alertasServidor, ...saida.alertas];

      // 7. Persistência. A ficha só recebe fatos seguros.
      const fichaNova = { ...(projeto.ficha ?? {}), ...saida.fatos_novos_ficha };
      await d.repo.salvarTurno({
        userId,
        projetoId: projeto_id,
        secaoId: secao_id,
        mensagemUsuario: mensagem,
        respostas: novasRespostas,
        resposta: saida,
        ficha: fichaNova,
        reabrir: acaoOk === "reabrir",
      });
      log({ status: 200, tipo: saida.tipo });
      return resposta({ ...saida, limite_caracteres: limite, uso: { usados, limite: d.limiteDiario } }, 200, cors);
    } catch (e) {
      if (e instanceof ErroLlm) {
        log({ status: 503, llm: e.tipo });
        if (e.tipo === "config") return erro(500, "configuracao", "O assistente está indisponível no momento.", cors);
        if (e.tipo === "timeout") return erro(504, "demorou", "A IA demorou demais. Tente de novo em instantes.", cors);
        if (e.tipo === "cota") return erro(503, "provedor_ocupado", "A IA gratuita está sobrecarregada agora. Tente de novo em alguns minutos.", cors);
        return erro(502, "provedor", "Não consegui falar com a IA agora. Tente de novo.", cors);
      }
      log({ status: 500, tipo: e instanceof ErroBanco ? "banco" : "interno" });
      return erro(500, "interno", "Algo deu errado do nosso lado. Seu texto não foi perdido; tente de novo.", cors);
    }
  };
}
