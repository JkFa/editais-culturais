// Base dos editores de tabela (orçamento, cronograma, mídia, equipe).
// O estado vive aqui; cada editor só desenha. Validação local com as MESMAS regras do servidor
// (tabelas.mjs). Salvamento automático quando todas as linhas estão válidas; o servidor revalida e
// recalcula os totais antes de gravar.
import { el } from "../formatos.js";
import { preencher } from "./dom.js";
import { normalizarLinha } from "./tabelas.mjs";

const ATRASO_SALVAR_MS = 1200;
export const novoId = () => (globalThis.crypto?.randomUUID ? crypto.randomUUID() : "00000000-0000-4000-8000-000000000000".replace(/0/g, () => ((Math.random() * 16) | 0).toString(16)));

export function criarControle({ tabela, api, projetoId, linhas, avisar, aoMudar }) {
  const c = {
    tabela,
    linhas: linhas.map((l) => ({ ...l })),
    tocadas: new Set(),
    status: "salvo", // salvo | pendente | salvando | invalido | erro
    mensagem: "",
    errosServidor: new Map(),
  };
  let relogio = null;
  const ouvintes = new Set();
  const avisarOuvintes = () => {
    ouvintes.forEach((f) => f());
    aoMudar?.(c);
  };
  c.aoAtualizar = (f) => ouvintes.add(f);

  /** Erros locais por id de linha (mesmas regras do servidor). */
  c.erros = () => {
    const m = new Map();
    for (const l of c.linhas) {
      const { erros } = normalizarLinha(tabela, l);
      if (Object.keys(erros).length) m.set(l.id, erros);
    }
    return m;
  };
  c.errosDe = (l) => {
    if (!c.tocadas.has(l.id) && !c.errosServidor.has(l.id)) return {};
    return { ...(c.erros().get(l.id) ?? {}), ...(c.errosServidor.get(l.id) ?? {}) };
  };
  c.tocar = (l) => {
    if (!c.tocadas.has(l.id)) c.tocadas.add(l.id);
  };

  c.adicionar = (padrao = {}) => {
    const l = { id: novoId(), ...padrao };
    c.linhas.push(l);
    c.agendar();
    return l;
  };
  c.remover = (l) => {
    c.linhas = c.linhas.filter((x) => x !== l);
    c.errosServidor.delete(l.id);
    c.agendar();
  };
  c.alterar = (l, campo, valor) => {
    l[campo] = valor;
    c.errosServidor.delete(l.id);
    c.agendar();
  };
  c.substituir = (novas) => {
    c.linhas = novas.map((l) => ({ ...l }));
    c.agendar();
  };

  c.agendar = () => {
    clearTimeout(relogio);
    c.status = "pendente";
    c.mensagem = "Alterações ainda não salvas.";
    avisarOuvintes();
    relogio = setTimeout(() => c.salvar(), ATRASO_SALVAR_MS);
  };

  c.salvar = async ({ forcar = false } = {}) => {
    clearTimeout(relogio);
    const invalidas = c.erros();
    if (invalidas.size) {
      invalidas.forEach((_, id) => c.tocadas.add(id));
      c.status = "invalido";
      c.mensagem = `${invalidas.size} linha(s) com problema. Corrija para salvar.`;
      avisarOuvintes();
      return false;
    }
    if (c.status === "salvo" && !forcar) return true;
    c.status = "salvando";
    c.mensagem = "Salvando…";
    avisarOuvintes();
    try {
      const r = await api.salvarItens(projetoId, tabela, c.linhas);
      c.status = "salvo";
      c.mensagem = "Salvo.";
      c.errosServidor.clear();
      c.ultimoResumo = r?.resumo ?? null;
      (r?.avisos ?? []).forEach((a) => avisar?.(a));
      avisarOuvintes();
      return true;
    } catch (e) {
      c.status = "erro";
      c.mensagem = e.message;
      for (const x of e.detalhes?.erros ?? []) {
        const l = c.linhas[x.indice];
        if (l) c.errosServidor.set(l.id, x.campos);
      }
      avisarOuvintes();
      return false;
    }
  };
  c.sujo = () => c.status === "pendente" || c.status === "salvando";
  return c;
}

/** Linha de status do editor (role=status): "Salvo", "Salvando…", "N linhas com problema". */
export function barraDeStatus(c) {
  const no = el("p", { class: `status-tabela st-${c.status}`, role: "status" });
  const desenhar = () => {
    no.className = `status-tabela st-${c.status}`;
    no.textContent = c.mensagem || "Salvo.";
  };
  c.aoAtualizar(desenhar);
  desenhar();
  return no;
}

let contador = 0;
/** Campo com rótulo, mensagem de erro ligada por aria e valor controlado pelo estado. */
export function campo(c, linha, nome, { rotulo, tipo = "text", opcoes, maxlength, placeholder, rows, aria, onInput, classe = "" }) {
  const id = `c${++contador}`;
  const erro = el("span", { id: `${id}-e`, class: "erro-campo", role: "alert" });
  const valor = linha[nome] ?? "";
  let entrada;
  if (opcoes) {
    entrada = el("select", { id }, opcoes.map(([v, t]) => el("option", { value: v, selected: v === valor || null }, t)));
  } else if (rows) {
    entrada = el("textarea", { id, rows, maxlength: maxlength ?? null, placeholder: placeholder ?? null });
    entrada.value = valor;
  } else {
    entrada = el("input", {
      id,
      type: tipo === "num" ? "text" : tipo,
      inputmode: tipo === "num" ? "decimal" : null,
      maxlength: maxlength ?? null,
      placeholder: placeholder ?? null,
    });
    entrada.value = valor === null ? "" : String(valor).replace(".", tipo === "num" ? "," : ".");
  }
  entrada.setAttribute("aria-describedby", `${id}-e`);
  const mostrarErro = () => {
    const e = c.errosDe(linha)[nome];
    erro.textContent = e ?? "";
    if (e) entrada.setAttribute("aria-invalid", "true");
    else entrada.removeAttribute("aria-invalid");
  };
  entrada.addEventListener(opcoes ? "change" : "input", () => {
    c.alterar(linha, nome, entrada.value);
    onInput?.(entrada.value);
    mostrarErro();
  });
  entrada.addEventListener("blur", () => {
    c.tocar(linha);
    mostrarErro();
  });
  c.aoAtualizar(mostrarErro);
  mostrarErro();
  const rot = el("label", { for: id }, rotulo);
  return { no: el("div", { class: `campo-tab ${classe}` }, rot, entrada, erro), entrada };
}

export const botao = (texto, aoClicar, classe = "btn btn-discreto", extra = {}) => el("button", { type: "button", class: classe, onclick: aoClicar, ...extra }, texto);
export { preencher };
