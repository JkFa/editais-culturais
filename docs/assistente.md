# Assistente de Projetos Culturais (Fase 1)

Escrita guiada, por conversa, de um projeto para um edital, seção por seção. A IA faz perguntas, aponta lacunas
e redige rascunhos **só com os fatos que a pessoa deu**. Onde falta informação, escreve `[COMPLETAR: ...]`.
O portal de editais continua 100% público; só o assistente pede login.

## Arquitetura

```
navegador (GitHub Pages, /projeto/, JS puro)
   │  login: link mágico  ──────────────►  Supabase Auth (GoTrue)  ◄── e-mail (SMTP)
   │  leitura/escrita de projetos, rascunhos, aprovação (JWT do usuário)
   ├──────────────────────────────────►  PostgREST + RLS (projetos, projeto_secoes, ...)
   │  POST /functions/v1/assistente  (JWT do usuário)
   └──────────────────────────────────►  Edge Function `assistente` (Deno)
                                            1. valida o JWT no GoTrue e o dono do projeto (RLS)
                                            2. consome o limite diário (RPC assistente_consumir_uso)
                                            3. monta o prompt SEM dados pessoais (privacidade.ts + prompts.ts)
                                            4. llm.ts: gerar(prompt, opcoes) ──► Gemini (Flash, plano gratuito)
                                            5. valida o JSON (contrato.ts), tenta de novo uma vez
                                            6. grava mensagens, respostas, rascunho e ficha (service_role)
specs/secoes/*.json  ──(scripts/sincronizar_specs.mjs)──►  web/projeto/specs.json  e  supabase/functions/assistente/specs.gen.json
```

| Parte | Onde |
|---|---|
| Migração, RLS, histórico, limite diário | `supabase/migrations/20261108000000_assistente.sql` (e `supabase/schema.sql`) |
| Edge Function | `supabase/functions/assistente/` (`index.ts`, `handler.ts`, `llm.ts`, `prompts.ts`, `contrato.ts`, `privacidade.ts`, `repositorio.ts`) |
| Specs das seções | `specs/secoes/*.json`, `specs/secao.schema.json`, `specs/checagens.json`, `specs/lib/secoes.mjs` |
| Front | `web/projeto/index.html`, `web/assets/js/projeto/*.js`, `web/assets/css/projeto.css` |

Rota do front: `/projeto/` (lista), `/projeto/?novo=1[&edital=<chave>]` (novo projeto), `/projeto/?id=<uuid>[&secao=<id>]`.
O GitHub Pages não tem reescrita de rotas; por isso são parâmetros de query, e não `/projeto/novo`.

## Privacidade (LGPD)

- Nome completo, CPF/CNPJ, endereço, telefone e e-mail de contato ficam em `projeto_proponente`, tabela que a função
  **nunca consulta**. Aparecem só na capa do `.docx`, gerada no navegador.
- Texto livre do usuário passa por `removerDadosPessoais` (CPF, CNPJ, e-mail, telefone, CEP) antes de ir ao LLM **e**
  antes de ir ao banco. O filtro não reconhece nomes: a interface avisa para não escrevê-los no chat.
- Da ficha só seguem chaves permitidas (`privacidade.ts`). Respostas de cotas ficam na ficha e só entram em prompt se a
  pessoa marcar "posso citar nos textos". A pergunta de cotas e a do edital têm `canal: "formulario"`: nunca passam pelo chat.
- Rede de segurança: o prompt final passa de novo pelo filtro. Os testes de snapshot (`prompts.test.ts`) plantam dados
  pessoais em todos os lugares e falham se algum aparecer.
- Os logs da função têm só ação, seção, status e duração. Sem texto, sem ficha, sem identificadores.
- **Atenção ao plano gratuito do Gemini:** pelos termos da Google (consultados em 07/11/2026), no plano gratuito
  prompts e respostas podem ser usados para melhorar produtos e revisados por pessoas. A interface e o `/sobre/`
  avisam isso. Para evitar, use um projeto Google com faturamento (plano pago) ou outro provedor.

## Rodar localmente

Pré-requisitos: Docker, Supabase CLI, Deno 2.x, Node 20+.

```bash
supabase start                                  # Postgres, Auth, PostgREST locais
supabase db reset                               # aplica as migrations
cat > supabase/functions/.env <<'ENV'           # fora do Git (.gitignore)
GEMINI_API_KEY=cole-sua-chave-aqui
LIMITE_DIARIO=60
ORIGENS_PERMITIDAS=http://localhost:8000
ENV
supabase functions serve assistente --env-file supabase/functions/.env
```

Para o front: crie `web/config.js` com a URL e a anon key locais (`supabase status`) e rode
`python -m http.server 8000 -d web`. O e-mail do link mágico chega no Inbucket (`http://127.0.0.1:54324`).

Testes (nenhum precisa de chave nem de rede):

```bash
node scripts/sincronizar_specs.mjs --check                   # cópias das specs em sincronia
deno test --allow-read --allow-env supabase/functions/assistente/   # função: prompts, LGPD, contrato, handler
pytest -q tests/test_assistente_specs.py tests/test_assistente_sql.py tests/test_assistente_site.py
```

Se mudar um prompt de propósito: `UPDATE_SNAPSHOTS=1 deno test --allow-read --allow-write --allow-env supabase/functions/assistente/prompts.test.ts`
e **revise o diff** dos arquivos em `__snapshots__/`.

## Variáveis e segredos

| Nome | Onde | Padrão | Para quê |
|---|---|---|---|
| `LLM_PROVIDER` | secret da função | `gemini` | `gemini` ou `groq` (stub) |
| `LLM_MODEL` | secret da função | `gemini-3.6-flash` | modelo do provedor (Flash estável em 07/11/2026; `gemini-2.5-flash` está obsoleto) |
| `GEMINI_API_KEY` | secret da função | — | chave do Google AI Studio. **Nunca** no repositório |
| `LIMITE_DIARIO` | secret da função | `60` | chamadas por usuário por dia (fuso de Brasília) |
| `ORIGENS_PERMITIDAS` | secret da função | `https://jkfa.github.io` | CORS (localhost sempre permitido) |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | automáticos na função | — | já existem no runtime do Supabase |

```bash
supabase secrets set GEMINI_API_KEY=... LLM_PROVIDER=gemini LLM_MODEL=gemini-3.6-flash LIMITE_DIARIO=60
```

O front só usa a anon key pública que o deploy do Pages já injeta em `web/config.js`.

## Trocar o provedor de LLM

Só `llm.ts` conhece o provedor. Para usar outro modelo do Gemini: `LLM_MODEL`. Para Groq: implementar
`ProvedorGroq.gerar` (o corpo da requisição está descrito no comentário do stub), `supabase secrets set
LLM_PROVIDER=groq GROQ_API_KEY=... LLM_MODEL=...`. Nenhum outro arquivo muda.

## Deploy

```bash
supabase db push                                   # aplica a migração (confira com `supabase db diff` antes)
supabase functions deploy assistente               # o config.toml já define verify_jwt = false
supabase secrets set GEMINI_API_KEY=...            # uma vez
```

`verify_jwt = false` é necessário porque o *preflight* CORS (`OPTIONS`) não leva JWT. A função valida o token no
GoTrue em toda chamada e confere que o projeto é do usuário (a leitura usa o JWT do usuário, então a RLS filtra).

### Autenticação (link mágico)

1. *Authentication → URL Configuration*: **Site URL** = `https://jkfa.github.io/editais-culturais/` e, em
   **Redirect URLs**, `https://jkfa.github.io/editais-culturais/projeto/**` (mais `http://localhost:8000/**` para testes).
   O mesmo vale em `supabase/config.toml` (`[auth]`) para o ambiente local.
2. **E-mail:** o SMTP padrão do Supabase envia pouquíssimos e-mails por hora e só para a equipe do projeto. Em produção
   configure um SMTP próprio gratuito (*Authentication → SMTP Settings*; Resend, Brevo ou similar).
3. **Google (opcional):** crie um cliente OAuth no Google Cloud, copie o *Client ID/Secret* em *Authentication →
   Providers → Google* e autorize a *Callback URL* que o Supabase mostra. O front já aceita o retorno
   (`#access_token=...`); falta apenas um botão que abra `…/auth/v1/authorize?provider=google&redirect_to=…`.

## Como adicionar uma seção (só criando um JSON)

1. Crie `specs/secoes/NN-id.json` (veja `specs/secao.schema.json`; `ativo: false` mostra "em breve"). Campos:
   `id`, `titulo`, `ordem`, `fluxos`, `depende_de` (seções cujo texto aprovado entra no contexto), `perguntas`,
   `condicionais` (`"se": "ficha.chave == true"`), `checagens` (ids de `specs/checagens.json`),
   `limite_caracteres_padrao`, `instrucoes_redacao`. Pergunta com `"canal": "formulario"` nunca passa pelo chat.
2. Se criou uma checagem nova, descreva-a em `specs/checagens.json`.
3. `node scripts/sincronizar_specs.mjs` (copia para o front e para a função) e `pytest tests/test_assistente_specs.py`.
4. `supabase functions deploy assistente` e publique o site. Nenhum código muda.

## Teste de RLS (documentado)

`tests/test_assistente_sql.py` sobe um Postgres 16 em Docker, aplica **todas** as migrations (duas vezes, para provar
idempotência) e confere, com papéis `anon`/`authenticated`/`service_role` e `auth.uid()` simulado:

- uma pessoa só lê, altera e apaga os **próprios** projetos e não cria projeto em nome de outra;
- `anon` não lê nenhuma tabela do assistente;
- seções, histórico, mensagens e dados do proponente de um projeto são invisíveis para outra pessoa;
- o histórico e as mensagens são somente leitura para o usuário (quem escreve é a função);
- o limite diário é atômico (1, 2, 3, depois -1) e só a `service_role` chama a RPC;
- excluir o edital não apaga o projeto (`edital_id` vira nulo; título e link ficam copiados no projeto).

## Roadmap

Fora desta fase, mas com ponto de extensão pronto: orçamento e cronograma em `.xlsx` (seções 10–11 já existem como
"em breve"); extração automática de critérios dos editais (a tabela `edital_criterios` já é consultada e usa valores
padrão quando vazia); Modo Parecerista; busca semântica (RAG/pgvector); fluxo audiovisual (`fluxo = 'audiovisual'` já
existe nas tabelas e nas specs); Recurso; conversa livre. Seções 6–11 (acessibilidade, democratização, comunicação,
equipe, cronograma, orçamento) entram criando JSONs.
