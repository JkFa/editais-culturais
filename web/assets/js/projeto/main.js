// Ponto de entrada de /projeto/: login, lista, novo projeto e tela do projeto (rotas por query string).
import { iniciarTema } from "../tema.js";
import { configurado } from "../dados.js";
import { el } from "../formatos.js";
import { criarApi } from "./api.js";
import { dadosDoToken, enviarLink, guardarSessao, limparHash, sair, sessaoAtiva, sessaoDoRetorno } from "./auth.js";
import { telaLista, telaNovo } from "./lista.js";
import { AVISO_IA, AVISO_PRIVACIDADE } from "./textos.js";
import { telaProjeto } from "./tela.js";

const config = window.EDITAIS_CONFIG ?? {};
const raiz = document.getElementById("projeto-raiz");

export function avisar(texto) {
  const alvo = document.getElementById("aviso-live");
  if (!alvo) return;
  alvo.textContent = texto;
  setTimeout(() => {
    if (alvo.textContent === texto) alvo.textContent = "";
  }, 4000);
}

async function carregarSpecs() {
  const r = await fetch(new URL("../../../projeto/specs.json", import.meta.url));
  if (!r.ok) throw new Error("specs");
  return r.json();
}


function telaLogin(erro) {
  const campo = el("input", { id: "email", type: "email", required: true, autocomplete: "email", inputmode: "email", placeholder: "voce@exemplo.com" });
  const msg = el("p", { class: "msg-form", role: "status" });
  const botao = el("button", { type: "submit", class: "btn btn-primario" }, "Receber link de acesso");
  const form = el(
    "form",
    {
      class: "form-login",
      onsubmit: async (ev) => {
        ev.preventDefault();
        botao.disabled = true;
        msg.className = "msg-form";
        msg.textContent = "Enviando…";
        try {
          await enviarLink(config, campo.value.trim(), location.origin + location.pathname + location.search);
          form.replaceWith(
            el("div", { class: "caixa-ok", role: "status" },
              el("p", {}, el("strong", {}, "Link enviado. "), `Abra o e-mail que mandamos para ${campo.value.trim()} neste mesmo navegador e clique no link. Pode levar alguns minutos; olhe também a caixa de spam.`),
              el("p", {}, el("a", { href: "" }, "Usar outro e-mail"))),
          );
        } catch (e) {
          msg.className = "msg-form erro";
          msg.textContent = e.message;
          botao.disabled = false;
        }
      },
    },
    el("label", { for: "email" }, "Seu e-mail"),
    campo,
    botao,
    msg,
  );
  raiz.replaceChildren(
    el("section", { class: "login", "aria-labelledby": "titulo-login" },
      el("h1", { id: "titulo-login" }, "Escreva seu projeto para um edital"),
      el("p", { class: "lead" }, "O assistente conversa com você, uma seção por vez, e escreve rascunhos só com o que você contar. Ele nunca inventa dados. O portal de editais continua aberto a todos; só o assistente pede login."),
      erro ? el("p", { class: "msg-form erro", role: "alert" }, erro) : null,
      form,
      el("p", { class: "nota" }, "Entramos por link no e-mail, sem senha."),
      el("p", { class: "nota" }, AVISO_PRIVACIDADE),
      el("p", { class: "nota" }, AVISO_IA)),
  );
}

function barraDoUsuario(email) {
  return el("div", { class: "barra-usuario" },
    el("span", {}, "Você entrou como ", el("strong", {}, email || "usuário")),
    el("button", { type: "button", class: "btn btn-discreto", onclick: async () => { await sair(config); location.href = location.pathname; } }, "Sair"));
}

async function iniciar() {
  iniciarTema();
  if (!configurado(config)) {
    raiz.replaceChildren(el("p", { class: "msg-form erro", role: "alert" }, "O assistente está indisponível: este site ainda não está ligado ao banco de dados."));
    return;
  }
  let erroLogin = null;
  const retorno = sessaoDoRetorno();
  if (retorno?.sessao) guardarSessao(retorno.sessao);
  if (retorno) limparHash();
  if (retorno?.erro) erroLogin = retorno.erro;

  const sessao = await sessaoAtiva(config);
  if (!sessao) return telaLogin(erroLogin);

  let specs;
  try {
    specs = await carregarSpecs();
  } catch {
    raiz.replaceChildren(el("p", { class: "msg-form erro", role: "alert" }, "Não consegui carregar as seções do assistente. Recarregue a página."));
    return;
  }
  const api = criarApi(config);
  const email = dadosDoToken(sessao.access_token).email;
  const ctx = { config, api, specs, raiz, email, avisar, barra: barraDoUsuario(email) };
  const params = new URLSearchParams(location.search);
  try {
    if (params.get("id")) await telaProjeto(ctx, params.get("id"), params.get("secao"));
    else if (params.has("novo")) await telaNovo(ctx, params.get("edital"));
    else await telaLista(ctx);
  } catch (e) {
    if (e.status === 401) {
      await sair(config);
      return telaLogin("Sua sessão expirou. Entre de novo.");
    }
    raiz.replaceChildren(ctx.barra, el("p", { class: "msg-form erro", role: "alert" }, e.message || "Algo deu errado. Recarregue a página."));
  }
}

iniciar();
