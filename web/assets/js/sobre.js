// Página "sobre" e 404: só tema, contagem de visita e data da última coleta.
import { config, iniciarPagina, preencherUltimaColeta } from "./comum.js";
import { carregarProgressivo } from "./dados.js";
import { iniciarTema } from "./tema.js";

// A 404 também carrega este módulo, mas não conta como visita.
if (location.pathname.includes("/sobre")) iniciarPagina("sobre");
else iniciarTema();
// O rodapé mostra a última coleta; o snapshot basta para isso.
carregarProgressivo({ ...config, supabaseUrl: "", supabaseAnonKey: "" }, (d) => preencherUltimaColeta(d.geradoEm)).catch(
  () => preencherUltimaColeta(null),
);

document.documentElement.dataset.pronto = "1"; // sinal para testes automatizados
