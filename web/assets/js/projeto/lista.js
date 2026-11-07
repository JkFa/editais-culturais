// "Meus projetos" e "Novo projeto".
import { preencher } from "./dom.js";
import { formatarData, el, plural } from "../formatos.js";

const urlProjeto = (id) => `?id=${encodeURIComponent(id)}`;

export async function telaLista(ctx) {
  const { api, specs, raiz } = ctx;
  const [projetos, aprovadas] = await Promise.all([api.listarProjetos(), api.aprovadasPorProjeto()]);
  const total = specs.secoes.filter((s) => s.ativo !== false).length;
  const porProjeto = new Map();
  for (const a of aprovadas) porProjeto.set(a.projeto_id, (porProjeto.get(a.projeto_id) ?? 0) + 1);

  const lista = el("ul", { class: "lista-projetos" });
  function cartao(p) {
    const feitas = Math.min(porProjeto.get(p.id) ?? 0, total);
    const apagar = el("button", { type: "button", class: "btn btn-discreto" }, "Excluir");
    let armado = null;
    apagar.addEventListener("click", async () => {
      if (!armado) {
        apagar.textContent = "Confirmar exclusão";
        apagar.classList.add("perigo");
        armado = setTimeout(() => {
          armado = null;
          apagar.textContent = "Excluir";
          apagar.classList.remove("perigo");
        }, 5000);
        return;
      }
      clearTimeout(armado);
      apagar.disabled = true;
      try {
        await api.excluirProjeto(p.id);
        item.remove();
        ctx.avisar("Projeto excluído.");
        const restantes = lista.children.length;
        const contagem = raiz.querySelector(".contagem-projetos");
        if (contagem) contagem.textContent = restantes ? `${plural(restantes, "projeto", "projetos")}.` : "";
        if (!restantes) raiz.querySelector(".vazio")?.removeAttribute("hidden");
      } catch (e) {
        apagar.disabled = false;
        ctx.avisar(e.message);
      }
    });
    const item = el("li", { class: "cartao-projeto" },
      el("div", { class: "cartao-projeto-info" },
        el("h2", {}, el("a", { href: urlProjeto(p.id) }, p.titulo)),
        el("p", { class: "fonte" }, p.edital_titulo
          ? ["Edital: ", p.edital_url ? el("a", { href: p.edital_url, target: "_blank", rel: "noopener noreferrer" }, p.edital_titulo) : p.edital_titulo]
          : "Projeto avulso (sem edital)"),
        el("p", { class: "progresso" },
          el("progress", { max: total, value: feitas, "aria-label": "Seções aprovadas" }),
          " ", el("span", { class: "num" }, `${feitas} de ${total}`), " seções aprovadas · atualizado em ", formatarData(p.updated_at))),
      el("div", { class: "cartao-projeto-acoes" },
        el("a", { class: "btn btn-primario", href: urlProjeto(p.id) }, "Abrir"), apagar));
    return item;
  }
  projetos.forEach((p) => lista.append(cartao(p)));

  preencher(raiz, 
    ctx.barra,
    el("div", { class: "topo-pagina" },
      el("h1", {}, "Meus projetos"),
      el("a", { class: "btn btn-primario", href: "?novo=1" }, "Novo projeto")),
    el("p", { class: "lead contagem-projetos" }, projetos.length ? plural(projetos.length, "projeto", "projetos") + "." : ""),
    el("p", { class: "vazio", hidden: projetos.length > 0 }, "Você ainda não tem projetos. Comece por um edital da lista ou crie um projeto avulso."),
    lista,
  );
}

export async function telaNovo(ctx, editalChave) {
  const { api, raiz } = ctx;
  let edital = null;
  let aviso = "";
  if (editalChave) {
    edital = await api.editalPorChave(editalChave).catch(() => null);
    if (!edital) aviso = "Não encontrei esse edital (ele pode ter sido encerrado e removido). Você pode buscar outro ou criar um projeto avulso.";
  }

  const titulo = el("input", { id: "titulo-projeto", type: "text", required: true, maxlength: 200, autocomplete: "off" });
  const mensagem = el("p", { class: "msg-form", role: "status" });
  const escolhido = el("div", { class: "edital-escolhido" });
  const resultados = el("ul", { class: "resultados-edital", "aria-label": "Editais encontrados" });
  const busca = el("input", { id: "busca-edital", type: "search", autocomplete: "off", placeholder: "Digite parte do nome do edital" });

  function escolher(e) {
    edital = e;
    if (e && !titulo.value) titulo.value = `Projeto para ${e.titulo}`.slice(0, 200);
    preencher(escolhido, 
      e
        ? el("p", {}, el("strong", {}, "Edital: "), e.titulo, " ", el("button", { type: "button", class: "btn btn-discreto", onclick: () => escolher(null) }, "Trocar"))
        : el("p", { class: "fonte" }, "Nenhum edital escolhido: o projeto será avulso."),
    );
    preencher(resultados);
    busca.closest(".grupo-busca").hidden = Boolean(e);
  }

  let temporizador;
  busca.addEventListener("input", () => {
    clearTimeout(temporizador);
    const t = busca.value.trim();
    if (t.length < 3) return preencher(resultados);
    temporizador = setTimeout(async () => {
      try {
        const achados = await api.buscarEditais(t);
        preencher(resultados, 
          ...(achados.length
            ? achados.map((e) => el("li", {}, el("button", { type: "button", class: "btn btn-discreto", onclick: () => escolher(e) }, e.titulo, el("span", { class: "fonte" }, ` · ${e.orgao_fomento}`))))
            : [el("li", { class: "fonte" }, "Nenhum edital aberto com esse nome.")]),
        );
      } catch (e) {
        preencher(resultados, el("li", { class: "msg-form erro" }, e.message));
      }
    }, 300);
  });

  const botao = el("button", { type: "submit", class: "btn btn-primario" }, "Criar projeto");
  const form = el("form", {
    class: "form-novo",
    onsubmit: async (ev) => {
      ev.preventDefault();
      botao.disabled = true;
      mensagem.className = "msg-form";
      mensagem.textContent = "Criando…";
      try {
        const ficha = edital
          ? { edital_tem_cotas: (edital.num_cotas ?? 0) > 0, ...(edital.teto_por_projeto ? { edital_teto_por_projeto: Number(edital.teto_por_projeto) } : {}) }
          : {};
        const criado = await api.criarProjeto({
          titulo: titulo.value.trim(),
          edital_id: edital?.id ?? null,
          edital_chave: edital?.chave ?? null,
          edital_titulo: edital?.titulo ?? null,
          edital_url: edital?.url_origem ?? null,
          ficha,
        });
        location.href = urlProjeto(criado.id);
      } catch (e) {
        mensagem.className = "msg-form erro";
        mensagem.textContent = e.message;
        botao.disabled = false;
      }
    },
  },
  el("div", { class: "grupo-busca" }, el("label", { for: "busca-edital" }, "Para qual edital? (opcional)"), busca, resultados),
  escolhido,
  el("label", { for: "titulo-projeto" }, "Nome do projeto"),
  titulo,
  el("p", { class: "nota" }, "Dica: use o nome que você daria ao projeto, como \"Oficinas de teatro de rua\". Você pode mudar depois."),
  botao, " ", el("a", { class: "btn btn-discreto", href: "./" }, "Cancelar"), mensagem);

  preencher(raiz, ctx.barra, el("h1", {}, "Novo projeto"), aviso ? el("p", { class: "msg-form erro", role: "alert" }, aviso) : null, form);
  escolher(edital);
}
