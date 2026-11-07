// Acesso aos dados do assistente: PostgREST (com o JWT do usuário, a RLS protege) e a Edge Function.
import { sessaoAtiva } from "./auth.js";

export class ErroApi extends Error {
  constructor(status, mensagem, codigo = "") {
    super(mensagem);
    this.status = status;
    this.codigo = codigo;
  }
}

const MENSAGENS = {
  401: "Sua sessão expirou. Entre de novo.",
  404: "Não encontrei isso. Talvez já tenha sido excluído.",
  429: "Você chegou ao limite de usos de hoje. Seu trabalho está salvo; volte amanhã.",
};

export function criarApi(config) {
  const raiz = config.supabaseUrl.replace(/\/$/, "");

  async function chamar(url, opcoes, tentarRenovar = true) {
    const sessao = await sessaoAtiva(config);
    if (!sessao) throw new ErroApi(401, MENSAGENS[401], "sem_sessao");
    let resposta;
    try {
      resposta = await fetch(url, {
        ...opcoes,
        headers: {
          apikey: config.supabaseAnonKey,
          Authorization: `Bearer ${sessao.access_token}`,
          ...opcoes.headers,
        },
      });
    } catch {
      throw new ErroApi(0, "Sem conexão com o servidor. Confira sua internet e tente de novo.", "rede");
    }
    if (resposta.status === 401 && tentarRenovar && (await sessaoAtiva(config, true))) return chamar(url, opcoes, false);
    if (!resposta.ok) {
      const corpo = await resposta.json().catch(() => ({}));
      throw new ErroApi(resposta.status, corpo.mensagem ?? MENSAGENS[resposta.status] ?? "Algo deu errado. Tente de novo.", corpo.erro ?? "");
    }
    return resposta.status === 204 ? null : resposta.json().catch(() => null);
  }

  const rest = (caminho, { metodo = "GET", corpo, prefer } = {}) =>
    chamar(`${raiz}/rest/v1/${caminho}`, {
      method: metodo,
      headers: { Accept: "application/json", ...(corpo !== undefined ? { "Content-Type": "application/json" } : {}), ...(prefer ? { Prefer: prefer } : {}) },
      body: corpo !== undefined ? JSON.stringify(corpo) : undefined,
    });

  const q = encodeURIComponent;
  return {
    rest,

    /** Chama a Edge Function. Lança ErroApi com a mensagem amigável do servidor. */
    assistente: (entrada) =>
      chamar(`${raiz}/functions/v1/assistente`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(entrada),
      }),

    listarProjetos: () => rest("projetos?select=id,titulo,edital_titulo,edital_url,updated_at&order=updated_at.desc&limit=100"),
    aprovadasPorProjeto: () => rest("projeto_secoes?select=projeto_id&aprovado=eq.true&limit=1000"),
    criarProjeto: (p) => rest("projetos?select=id", { metodo: "POST", corpo: p, prefer: "return=representation" }).then((l) => l[0]),
    excluirProjeto: (id) => rest(`projetos?id=eq.${q(id)}`, { metodo: "DELETE" }),
    carregarProjeto: (id) => rest(`projetos?id=eq.${q(id)}&select=*&limit=1`).then((l) => l[0] ?? null),
    atualizarProjeto: (id, campos) => rest(`projetos?id=eq.${q(id)}`, { metodo: "PATCH", corpo: campos }),

    secoesDoProjeto: (id) => rest(`projeto_secoes?projeto_id=eq.${q(id)}&select=id,secao_id,rascunho,versao,aprovado,updated_at`),
    secoesComConversa: (id) => rest(`mensagens?projeto_id=eq.${q(id)}&select=secao_id&limit=1000`).then((l) => new Set(l.map((m) => m.secao_id))),
    mensagens: (id, secao) => rest(`mensagens?projeto_id=eq.${q(id)}&secao_id=eq.${q(secao)}&select=papel,conteudo,created_at&order=created_at.asc&limit=200`),
    historico: (secaoRowId) => rest(`projeto_secoes_hist?projeto_secao_id=eq.${q(secaoRowId)}&select=versao,rascunho,created_at&order=versao.desc&limit=30`),

    salvarRascunho: (projetoId, secaoId, rascunho) =>
      rest("projeto_secoes?on_conflict=projeto_id,secao_id&select=id,versao,aprovado,updated_at", {
        metodo: "POST",
        corpo: { projeto_id: projetoId, secao_id: secaoId, rascunho },
        prefer: "resolution=merge-duplicates,return=representation",
      }).then((l) => l[0]),
    definirAprovacao: (projetoId, secaoId, aprovado) =>
      rest(`projeto_secoes?projeto_id=eq.${q(projetoId)}&secao_id=eq.${q(secaoId)}`, { metodo: "PATCH", corpo: { aprovado } }),

    proponente: (id) => rest(`projeto_proponente?projeto_id=eq.${q(id)}&select=dados&limit=1`).then((l) => l[0]?.dados ?? {}),
    salvarProponente: (id, dados) =>
      rest("projeto_proponente?on_conflict=projeto_id", { metodo: "POST", corpo: { projeto_id: id, dados }, prefer: "resolution=merge-duplicates,return=minimal" }),

    /** Editais públicos (leitura anônima, mas o JWT do usuário também vale). */
    editalPorChave: (chave) =>
      rest(`editais?chave=eq.${q(chave)}&select=id,chave,titulo,orgao_fomento,url_origem,num_cotas,teto_por_projeto,linguagens&limit=1`).then((l) => l[0] ?? null),
    buscarEditais: (texto) =>
      rest(`editais_abertos?titulo=ilike.${q(`*${texto.replace(/[*%,()]/g, " ").trim()}*`)}&select=id,chave,titulo,orgao_fomento,url_origem,num_cotas,teto_por_projeto&order=inscricao_fim.asc&limit=8`),
  };
}
