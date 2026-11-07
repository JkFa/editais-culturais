// LGPD: nada de dado pessoal do proponente vai para o LLM (nem fica no banco por este caminho).
// Camadas: (1) os dados pessoais ficam em tabela própria que a função nunca lê; (2) o texto livre
// do usuário passa por `removerDadosPessoais`; (3) a ficha só leva chaves permitidas; (4) o
// prompt final passa de novo pelo filtro, como rede de segurança.

export const MARCA = "[DADO PESSOAL REMOVIDO]";

const PADROES: Array<[string, RegExp]> = [
  ["cnpj", /\b\d{2}\.?\d{3}\.?\d{3}\/?\d{4}-?\d{2}\b/g],
  ["cpf", /\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|\b\d{11}\b/g],
  ["email", /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/g],
  ["telefone", /(?:\+55\s?)?\(\d{2}\)\s?9?\d{4}[-\s]?\d{4}|\+55\s?\d{2}\s?9?\d{4}[-\s]?\d{4}|\b\d{2}\s9\d{4}[-\s]?\d{4}\b|\b9?\d{4}-\d{4}\b/g],
  ["cep", /\b\d{5}-\d{3}\b/g],
];

export function removerDadosPessoais(texto: string): { texto: string; removidos: string[] } {
  let saida = texto;
  const removidos: string[] = [];
  for (const [tipo, re] of PADROES) {
    saida = saida.replace(re, () => {
      removidos.push(tipo);
      return MARCA;
    });
  }
  return { texto: saida, removidos: [...new Set(removidos)] };
}

/** Chaves da ficha que nunca vão ao LLM (dados pessoais e respostas sensíveis de cotas). */
const CHAVE_PROIBIDA = /(^|_)(cpf|cnpj|rg|nome_completo|nome_civil|endereco|rua|cep|telefone|celular|whatsapp|email|e_mail|contato|nascimento|raca|cor|etnia|pcd|deficiencia|orientacao|genero)(_|$)|^cotas?(_|$)/i;

export function chaveProibida(chave: string): boolean {
  return CHAVE_PROIBIDA.test(chave);
}

function limparValor(v: unknown, profundidade = 0): unknown {
  if (typeof v === "string") return removerDadosPessoais(v).texto;
  if (profundidade > 2) return undefined;
  if (Array.isArray(v)) return v.slice(0, 20).map((x) => limparValor(x, profundidade + 1)).filter((x) => x !== undefined);
  if (v && typeof v === "object") return fichaPermitida(v as Record<string, unknown>, profundidade + 1);
  return v;
}

/**
 * Ficha como o LLM pode vê-la. As chaves de cotas só entram se a própria pessoa marcou
 * `cotas_usar_no_texto` (e, mesmo assim, o texto passa pelo filtro).
 */
export function fichaPermitida(ficha: Record<string, unknown>, profundidade = 0): Record<string, unknown> {
  const usarCotas = ficha["cotas_usar_no_texto"] === true;
  const saida: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(ficha)) {
    if (k === "cotas_usar_no_texto") continue;
    if (chaveProibida(k) && !(usarCotas && /^cotas?(_|$)/i.test(k))) continue;
    const limpo = limparValor(v, profundidade);
    if (limpo !== undefined && limpo !== "") saida[k] = limpo;
  }
  return saida;
}

/** Fatos que o LLM quer gravar na ficha: mesmas regras (chaves e valores). */
export function fatosSeguros(fatos: Record<string, unknown>): Record<string, unknown> {
  const saida: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(fatos)) {
    if (!/^[a-z][a-z0-9_]{1,40}$/.test(k) || chaveProibida(k)) continue;
    const limpo = limparValor(v);
    if (limpo !== undefined && limpo !== "") saida[k] = limpo;
  }
  return saida;
}
