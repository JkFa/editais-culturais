// Formulário dos dados pessoais do proponente. Ficam em tabela própria (projeto_proponente),
// aparecem só na capa do .docx e NUNCA são enviados à IA.
import { el } from "../formatos.js";

export const CAMPOS_PROPONENTE = [
  ["nome_completo", "Nome completo (ou razão social)", "text", "name"],
  ["documento", "CPF ou CNPJ", "text", "off"],
  ["endereco", "Endereço", "text", "street-address"],
  ["telefone", "Telefone", "tel", "tel"],
  ["email_contato", "E-mail de contato", "email", "email"],
];

export function dialogo(titulo, conteudo, aoFechar) {
  const d = el("dialog", { class: "dialogo", "aria-labelledby": "dialogo-titulo" },
    el("div", { class: "dialogo-topo" }, el("h2", { id: "dialogo-titulo" }, titulo),
      el("button", { type: "button", class: "btn", onclick: () => d.close() }, "Fechar")),
    conteudo);
  d.addEventListener("close", () => { d.remove(); aoFechar?.(); });
  d.addEventListener("click", (ev) => { if (ev.target === d) d.close(); });
  document.body.append(d);
  d.showModal();
  return d;
}

export async function abrirProponente(ctx, projeto) {
  const dados = await ctx.api.proponente(projeto.id).catch(() => ({}));
  const campos = CAMPOS_PROPONENTE.map(([chave, rotulo, tipo, auto]) => {
    const input = el("input", { id: `prop-${chave}`, type: tipo, autocomplete: auto, maxlength: 200, value: dados[chave] ?? "" });
    return { chave, input, linha: el("div", { class: "campo" }, el("label", { for: `prop-${chave}` }, rotulo), input) };
  });
  const msg = el("p", { class: "msg-form", role: "status" });
  const form = el("form", {
    onsubmit: async (ev) => {
      ev.preventDefault();
      try {
        await ctx.api.salvarProponente(projeto.id, Object.fromEntries(campos.map((c) => [c.chave, c.input.value.trim()])));
        msg.className = "msg-form";
        msg.textContent = "Dados salvos.";
        ctx.avisar("Dados do proponente salvos.");
      } catch (e) {
        msg.className = "msg-form erro";
        msg.textContent = e.message;
      }
    },
  },
  el("p", { class: "nota" }, "Estes dados ficam só no seu projeto, aparecem apenas na capa do arquivo .docx e nunca são enviados à IA."),
  campos.map((c) => c.linha),
  el("button", { type: "submit", class: "btn btn-primario" }, "Salvar dados"), msg);
  dialogo("Dados do proponente", form);
}
