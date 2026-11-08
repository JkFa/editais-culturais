# Assistente de Projetos Culturais (Fases 1 e 2)

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

## Fase 2: orçamento, cronograma, mídia, equipe e critérios dos editais

Seções 6–11 (acessibilidade, democratização, comunicação, equipe, cronograma, orçamento) estão ativas, com perguntas e
condicionais nos JSONs de `specs/secoes/`. As quatro últimas têm **editor de tabela** além do chat.

### Tabelas do projeto

`orcamento_itens`, `cronograma_itens`, `midia_itens`, `equipe_membros` (migration `20261109120000_assistente_fase2.sql`).
O usuário só tem **SELECT** pela RLS; a escrita passa pela função `assistente` (ação `salvar_itens`, sem LLM e sem gastar o limite
diário), que valida cada linha, confere que ids e ligações pertencem ao projeto e grava com `service_role`.
Regras puras (totais, etapas, valor a confirmar, checagens) ficam em `specs/lib/*.mjs`, **o mesmo código** no front e na função
(`scripts/sincronizar_specs.mjs`, conferido no CI com `--check`).

- **Preço nunca vem da IA.** A função só aceita um valor proposto pelo modelo se o número aparece em algo que a pessoa escreveu;
  senão grava `null` e o editor mostra "valor a confirmar". Nomes da equipe só se a pessoa os escreveu; nomes e currículos da equipe
  nunca entram no prompt.
- **Propostas:** o rascunho pode trazer `itens_propostos`; o painel mostra cada um para aceitar, editar ou descartar.
- **Pendências do projeto:** painel com as checagens cruzadas (total acima do teto, percentual de etapa fora do limite do edital,
  mídia sem custo no orçamento, equipe sem item de orçamento, cronograma além do prazo, etc.). Catálogo em `specs/checagens.json`.

### Exportação

- **.xlsx** (ExcelJS, carregado do CDN, gerado no navegador): abas Orçamento, Resumo, Cronograma, Plano de mídia, com **fórmulas de
  verdade** (`=E*F`, `SUBTOTAL`, `SUMIF` entre abas, `IF/AND` para a situação), formatação condicional e cabeçalho congelado.
  Testado no LibreOffice (`tests/test_assistente_xlsx.py`): mudar uma quantidade recalcula tudo.
- **.docx:** seções 6–11 com tabelas-resumo (orçamento por etapa, cronograma, mídia, equipe sem dados pessoais).
- **Copiar por campo:** segue `campos_exigidos` do edital, quando extraído.

### Critérios dos editais (extração automática)

`scripts/extrair_criterios/` roda no GitHub Actions (job `criterios` em `.github/workflows/sync_editais.yml`, **depois** da coleta,
com timeout próprio e `continue-on-error`: se falhar, a coleta já foi commitada).

1. Planeja a fila (`extracao_fila`): editais brasileiros abertos, os com mais projetos primeiro, depois o prazo mais próximo; no máximo
   `MAX_EDITAIS_POR_DIA` por execução (padrão **20**, variável do repositório). Reconfere um edital já extraído se o link mudou ou
   passaram 30 dias; erro tenta de novo até 3 vezes.
2. Baixa o PDF (ou a página) com `ClienteWeb` (respeita `robots.txt`, User-Agent identificado, intervalo entre pedidos). **403 não é
   contornado:** o edital vira `sem_documento`. PDFs com mais de 8 MB e PDFs escaneados (sem texto) também.
3. Compara o hash com o da extração anterior: documento igual **não chama o LLM**.
4. Texto por `pdftotext`/`pypdf`, dividido em partes por página; Gemini com *structured output* e temperatura 0.
5. **Validação:** cada valor precisa vir com um `trecho` que **exista literalmente** no documento (comparação normalizada) e, para
   números, o número tem de aparecer no trecho. Sem isso o campo é descartado (fica `null`, nunca inventado). Pesos em fração que não
   somam 1 são removidos. Trecho gravado com até 200 caracteres e página.
6. Grava `edital_criterios` com `revisado = false`, `fonte_url`, `fonte_hash`, `modelo_llm` e `evidencias`.

O assistente usa os critérios na hora de perguntar e de checar (teto, percentuais, limites de caracteres, campos exigidos) e o front
mostra **"critérios lidos automaticamente — confira no edital"** enquanto `revisado = false`. Sem critérios, tudo continua como na Fase 1.

| Nome | Onde | Padrão | Para quê |
|---|---|---|---|
| `GEMINI_API_KEY` | **GitHub Secret** do repositório | — | extração. Sem ela o job só atualiza a fila |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | GitHub Secrets (já existem) | — | gravar fila e critérios |
| `MAX_EDITAIS_POR_DIA` | variável do repositório | `20` | teto de editais por execução (cota gratuita do Gemini) |
| `GEMINI_MODEL` | variável do repositório | `gemini-3.6-flash` | modelo da extração |

Rodar à mão: workflow "Sincronizar editais" com `somente_criterios` (e `edital_criterios` para um só). Local:
`python -m scripts.extrair_criterios --dry-run --max 3 --edital <id>` (precisa de `SUPABASE_*` e `GEMINI_API_KEY` no ambiente).
Custo: zero na camada gratuita; por edital são de 3 a 8 chamadas (documento em partes). Limite conhecido: critérios que estão em
anexo separado do edital não são lidos (a observação diz isso).

### Revisão pelo administrador

`/admin/criterios/` (fora do menu, `noindex`, `Disallow: /admin/` no `robots.txt`). Só quem está na tabela `admins` vê dados; a
proteção real é a RLS (`eh_admin()`), a tela só reflete. Lista "Para revisar" ordenada pelo número de projetos abertos
(`admin_criterios_para_revisar()`), aba "Com problema" (erro e sem documento), edição campo a campo com a citação do edital e link
para a página do documento, e os botões **Marcar como revisado** (grava quem e quando), **Reprocessar** (volta à fila com
`ultimo_erro = 'reprocessar'`, o que força nova extração mesmo com hash igual) e **Sem critérios aproveitáveis** (pede confirmação).

Cadastrar um administrador (uma vez, depois de a pessoa entrar pelo assistente para existir em `auth.users`):

```sql
insert into public.admins (user_id) select id from auth.users where email = 'EMAIL_DO_ADMIN';
```

### Deploy da Fase 2 (ordem)

1. Aplicar a migration (`supabase db push --dry-run`, depois `supabase db push`). O front tolera as tabelas ausentes, mas a função nova não.
2. `supabase functions deploy assistente --use-api --no-verify-jwt`.
3. Merge do PR (publica o front) e `gh secret set GEMINI_API_KEY`.
4. Rodar o workflow com `somente_criterios`; cadastrar o administrador e revisar.

## Roadmap (Fase 3)

Modo Parecerista (a IA avalia o projeto pelos critérios do edital, já extraídos e revisados); busca semântica (RAG/pgvector) sobre
editais e projetos aprovados; fluxo audiovisual (`fluxo = 'audiovisual'` já existe nas tabelas e nas specs); Recurso; conversa livre;
classificação objetivo × meta por LLM (hoje a ligação é manual).
