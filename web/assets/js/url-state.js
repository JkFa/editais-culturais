// Estado <-> URL. Abrir um link compartilhado reproduz exatamente a mesma tela.
import { LINGUAGENS, ORDENS, PERFIS, TIPOS_APOIO, UFS, estadoInicial } from "./filtros.js";
import { AMBITOS } from "./internacional.js";

export function lerEstado(busca = location.search) {
  const p = new URLSearchParams(busca);
  const estado = estadoInicial();
  estado.q = (p.get("q") ?? "").slice(0, 120);
  if (PERFIS.some((x) => x.id === p.get("perfil"))) estado.perfil = p.get("perfil");
  estado.linguagens = (p.get("ling") ?? "").split(",").filter((l) => LINGUAGENS.includes(l));
  if (p.get("abr") === "nacional") estado.abrangencia = "nacional";
  if (UFS.includes(p.get("uf"))) {
    estado.uf = p.get("uf");
    estado.abrangencia = "";
  }
  if (TIPOS_APOIO.some((t) => t.id === p.get("apoio"))) estado.tipoApoio = p.get("apoio");
  if (AMBITOS.some((a) => a.id === p.get("amb"))) estado.ambito = p.get("amb");
  if (["brasileira", "outra"].includes(p.get("nac"))) estado.nacionalidade = p.get("nac");
  if (/^([A-Z]{2}|OUTRO)$/.test(p.get("res") ?? "")) estado.residencia = p.get("res");
  if (["sim", "nao"].includes(p.get("org"))) estado.organizacao = p.get("org");
  estado.apenasAbertas = p.get("abertas") !== "0";
  if (ORDENS.some((o) => o.id === p.get("ord"))) estado.ordem = p.get("ord");
  if (p.get("aba") === "favoritos") estado.aba = "favoritos";
  return { estado, edital: p.get("edital") || null };
}

export function montarBusca(estado, edital = null) {
  const p = new URLSearchParams();
  if (estado.q) p.set("q", estado.q);
  if (estado.perfil) p.set("perfil", estado.perfil);
  if (estado.linguagens.length) p.set("ling", estado.linguagens.join(","));
  if (estado.abrangencia) p.set("abr", estado.abrangencia);
  if (estado.uf) p.set("uf", estado.uf);
  if (estado.tipoApoio) p.set("apoio", estado.tipoApoio);
  if (estado.ambito !== "brasil") p.set("amb", estado.ambito);
  if (estado.nacionalidade) p.set("nac", estado.nacionalidade);
  if (estado.residencia) p.set("res", estado.residencia);
  if (estado.organizacao) p.set("org", estado.organizacao);
  if (!estado.apenasAbertas) p.set("abertas", "0");
  if (estado.ordem !== "prazo") p.set("ord", estado.ordem);
  if (estado.aba === "favoritos") p.set("aba", "favoritos");
  if (edital) p.set("edital", edital);
  const texto = p.toString();
  return texto ? `?${texto}` : location.pathname;
}

/** Filtros mudam com replaceState (não poluem o histórico); preserva o state do painel. */
export function gravarEstado(estado, edital = null) {
  history.replaceState(history.state, "", montarBusca(estado, edital));
}

/** Abrir o painel empilha uma entrada: o botão voltar fecha o painel. */
export function empilharPainel(estado, edital) {
  history.pushState({ painel: true }, "", montarBusca(estado, edital));
}

export function urlDoPortal(chave) {
  const base = location.href.split(/[?#]/)[0];
  return `${base}?edital=${encodeURIComponent(chave)}`;
}
