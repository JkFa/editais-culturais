# Editais Culturais do Brasil

Portal que reúne editais e chamamentos culturais abertos (Música, Cinema & Audiovisual, Teatro & Dança,
Fotografia, Artes Visuais, Literatura, Patrimônio, Multiáreas) para que artistas e produtores vejam em
segundos se podem se inscrever: prazo, valor, quem pode participar e o link oficial.

Custo zero: GitHub Pages + GitHub Actions + Supabase Free + um snapshot JSON versionado.

```
Fontes ──► worker/ (Python, httpx async) ──► Supabase (tabela editais)
                     │
                     └──► web/data/snapshot.json  (commit diário pelo Action)

web/ (GitHub Pages)
  1. tenta Supabase (se config.js tiver URL + anon key; timeout de 4 s)
  2. se falhar ou não houver config → carrega data/snapshot.json
  3. o rodapé mostra qual fonte está ativa e a data da coleta
```

O snapshot é commitado todo dia. Isso dá um fallback sempre real e atualizado, mantém o repositório
ativo (o GitHub desativa workflows agendados após 60 dias sem atividade) e faz o site continuar no ar se
o Supabase pausar ou estourar a cota.

## Ver o site agora (sem configurar nada)

```bash
python3 -m http.server 8000 -d web
# abra http://localhost:8000
```

Precisa de um servidor HTTP (não abre por `file://`, porque usa módulos ES e `fetch`). Sem `config.js`
preenchido, o site lê o `web/data/snapshot.json`, que tem dados reais.

## Colocar no ar em ~15 minutos

Os comandos de `git` e a criação do repositório remoto são com você; nada neste projeto faz push.

### 1. Supabase (5 min)

1. Crie um projeto em <https://supabase.com> (plano Free).
2. Abra **SQL Editor**, cole o conteúdo de `supabase/schema.sql` e rode. É idempotente: pode rodar de novo.
3. Em **Project Settings > API**, copie:
   - **Project URL** (`https://xxxx.supabase.co`);
   - chave **anon / public** (vai para o site; é pública por design, a segurança vem da RLS);
   - chave **service_role** (**secreta**: só o worker usa; nunca no site nem em arquivos versionados).

### 2. GitHub (5 min)

1. Crie o repositório, envie o código e deixe a branch padrão como `main`:
   ```bash
   git branch -M main
   git remote add origin git@github.com:SEU-USUARIO/editais-culturais.git
   git push -u origin main
   ```
2. **Settings > Secrets and variables > Actions > Secrets** (aba *Secrets*):
   | nome | valor |
   |---|---|
   | `SUPABASE_URL` | Project URL |
   | `SUPABASE_SERVICE_ROLE_KEY` | chave service_role |
   | `GEMINI_API_KEY` | opcional: liga o enriquecimento por LLM |
3. Mesma tela, aba **Variables**:
   | nome | valor |
   |---|---|
   | `SUPABASE_URL` | Project URL |
   | `SUPABASE_ANON_KEY` | chave anon / public |
4. **Settings > Pages > Build and deployment > Source: GitHub Actions**.
5. **Actions > Sincronizar editais > Run workflow**. Ao terminar, o workflow "Publicar site" roda sozinho.
   O site fica em `https://SEU-USUARIO.github.io/editais-culturais/`.

Sem os secrets do Supabase, a coleta roda em modo `--dry-run` (só gera o snapshot) em vez de falhar.
Sem as variáveis `SUPABASE_*`, o site publica com `config.js` vazio e usa o snapshot. O deploy aborta se
a variável `SUPABASE_ANON_KEY` contiver, por engano, a chave `service_role`.

A coleta roda todo dia às 05h de Brasília (`cron 0 8 * * *`) e também sob demanda.

### 3. Conferir

- O rodapé do site diz "Dados ao vivo" (Supabase) ou "Dados de DD/MM/AAAA HH:mm (cópia offline)".
- No Supabase, a tabela `heartbeat` registra o último ping do worker.

## Rodar o worker localmente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m worker.scraper_worker --dry-run     # coleta, padroniza e escreve web/data/snapshot.json
```

`--dry-run` funciona sem nenhuma variável de ambiente. Para gravar no Supabase, copie `.env.example` para
`.env` (ignorado pelo git), preencha e rode sem `--dry-run`.

| variável | para quê |
|---|---|
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | gravar no banco (sem elas, vira `--dry-run`) |
| `GEMINI_API_KEY` | enriquecimento LLM opcional (valor, proponentes, contrapartida, resumo) |
| `GEMINI_MODEL`, `GEMINI_MAX_POR_EXECUCAO` | modelo (padrão `gemini-2.5-flash`) e teto de chamadas por execução |

Código de saída: `0` ok; `1` se **todas** as fontes falharam (o snapshot anterior é preservado); `3` se a
coleta funcionou mas a gravação no Supabase falhou (o snapshot já foi escrito).

## Fontes

- **A. Mapas Culturais** (`worker/fontes.yaml`): API pública `/api/opportunity/find`. Instâncias ativas
  hoje: Brasil (MinC), CE, PE, SE, PA, PB, Aracaju, Natal e Campinas. Para acrescentar uma, inclua um bloco
  em `instancias:` (slug, `base_url`, abrangência, UF). Se uma instância sair do ar, marque `ativa: false`
  e escreva o `motivo`.
- **B. Curadoria manual** (`worker/fontes_curadas.yaml`): editais de quem não usa Mapas Culturais (Funarte,
  Itaú Cultural, Sesc etc.). É a única fonte que preenche valor, proponentes e contrapartida "à mão".

### Como acrescentar entradas à curadoria

Só entra edital conferido na fonte oficial ou na plataforma de inscrição. Nunca de memória.

Em `editais:` (abertos agora):

```yaml
  - id_externo: <slug-unico-e-estavel>
    titulo: "<título como na fonte>"
    orgao_fomento: "<órgão>"
    url_origem: "<página oficial>"
    url_inscricao: "<onde se inscreve, ou null>"
    url_edital_pdf: "<PDF, ou null>"
    resumo_simples: "<até 280 caracteres, sem juridiquês>"
    linguagens: ["<uma ou mais da taxonomia>"]   # Música, Cinema & Audiovisual, Teatro & Dança,
                                                 # Fotografia, Artes Visuais, Literatura, Patrimônio, Multiáreas
    abrangencia: nacional                        # nacional | estadual | municipal
    uf: null
    municipio: null
    proponentes: ["PF", "MEI", "PJ_COM_FINS", "PJ_SEM_FINS", "COLETIVO"]   # só os comprovados; ou null
    valor_total: null                            # número em reais, só se comprovado
    teto_por_projeto: null
    tipo_apoio: null                             # cessao_de_espaco mostra "Cessão de espaço (sem repasse)"
    exige_contrapartida: null                    # true/false só se comprovado
    marco_legal: null
    documentos_exigidos: []
    inscricao_inicio: "AAAA-MM-DD"
    inscricao_fim: "AAAA-MM-DD"                  # sem horário confirmado; com horário: "AAAA-MM-DDTHH:MM:SS-03:00"
    fim_tem_horario: false                       # true só com horário confirmado
    verificado_em: "AAAA-MM-DD"                  # data em que VOCÊ conferiu a fonte
    pendencias: null                             # o que falta confirmar
    evidencias:                                  # trecho LITERAL da fonte, por campo
      inscricao_fim: "<trecho>"
      proponentes: "<trecho>"
```

Regras que o worker valida (erro explícito, a curadoria inteira é rejeitada se alguma entrada falhar):

- `inscricao_fim`, `proponentes`, `valor_total`, `teto_por_projeto` e `exige_contrapartida`, quando
  preenchidos, exigem o trecho correspondente em `evidencias`. Para valores, o trecho precisa conter o número.
- `tipo_apoio: cessao_de_espaco` não pode ter valor em R$.
- Campo sem comprovação fica `null`: o site mostra "Não informado no resumo — ver edital".
- Se `verificado_em` tiver mais de 45 dias, o log do Action emite um aviso. Reconfira a fonte pelo menos
  uma vez por mês.

Em `radar:` ficam os editais recorrentes hoje **fechados**. Eles aparecem na seção discreta "Costuma abrir
em breve" (ordenada pela proximidade da `janela_habitual`) e **nunca** são contados como abertos. O mês é
lido de `janela_habitual` ("fim de janeiro a março" → janeiro a março).

## Como os dados são tratados

Este portal orienta pessoas sobre dinheiro público e prazos, então dado errado causa dano real:

- Nada é inventado, nem em exemplos ou fallback. Os testes usam fixtures reais (`tests/fixtures/`).
- Todo campo derivado é anulável e guarda a proveniência (`regex`, `llm`, `curadoria` ou `api`) com o trecho
  literal do texto-fonte. No painel do edital, "Como sabemos disso" mostra esses trechos.
- Valor extraído por LLM só é aceito se o trecho existir literalmente no texto e contiver o número.
- Campo nulo aparece como "Não informado no resumo — ver edital", nunca como zero.

## Testes e verificações

```bash
pytest                                  # unitários + ponta a ponta (o e2e pula sozinho sem Chromium)
playwright install chromium             # uma vez, para o e2e do site
ruff check .
```

O e2e (`tests/test_e2e_site.py`) sobe o site e confere: cards do snapshot, zero erros no console, 3 filtros
mantidos ao recarregar a URL, painel com teclado (foco preso, Esc, botão voltar), favoritos persistentes,
exportações CSV/.ics, cópia para WhatsApp (com e sem `navigator.clipboard`), modo ao vivo simulado e
fallback para o snapshot. Salva screenshots em 375 px e 1280 px em `verificacao/` (ignorada pelo git).

## Estrutura

```
supabase/schema.sql        tabelas, índices, view, RLS, limpeza
worker/                    ETL (coletores, padronização, LLM opcional, persistência)
web/                       site estático (HTML + ES modules + um CSS; sem build)
tests/                     pytest + Playwright; fixtures reais em tests/fixtures
.github/workflows/         sync_editais.yml (coleta diária) e deploy_pages.yml
DECISIONS.md               decisões, descobertas da Fase 0 e desvios da especificação
```

## Limitações conhecidas

- **Cobertura**: só Mapas Culturais (9 instâncias) e a curadoria manual. Itaú Cultural, Sesc, Funarte e
  similares dependem de alguém manter `fontes_curadas.yaml`. Em 06/11/2026 não havia edital aberto do
  Itaú Cultural nem do Sesc; eles estão no `radar`. O Mapa Cultural do DF devolve 403 a clientes automatizados.
- **Ruído nos Mapas Culturais**: qualquer pessoa publica ali. Há anúncios e testes marcados como "Edital".
  Filtramos por tipo e por regex de título, mas ainda pode passar item que não seja fomento.
- **Só o resumo, não o PDF**: valores e proponentes vêm da descrição publicada na plataforma, não do PDF do
  edital. Por isso muitos editais mostram "Não informado no resumo — ver edital". Contrapartida, documentos,
  cronograma e número de cotas só aparecem para a curadoria (ou LLM, com evidência).
- **Heurísticas**: o regex de valores lê o contexto da frase (total × teto por projeto) e descarta taxas e
  remuneração de parecerista, mas pode errar em textos atípicos; sempre confira na página oficial.
  "Pessoa jurídica" sem qualificação conta como com e sem fins lucrativos. Vários tetos no mesmo edital: usa o maior.
- **Prazos**: o Mapas sempre grava hora. `00:00` e `23:59` são tratados como "só o dia" (fim às 23:59:59).
  Fluxo contínuo (fim em 2100/2111) é omitido por não ter prazo real.
- **Abrangência desconhecida** (editais do Mapa federal sem UF explícita) não aparece sob o filtro "Nacional"
  nem sob um estado.
- **A soma do cabeçalho** ("R$ X em N editais com valor informado") soma totais declarados de editais
  diferentes; não é orçamento público consolidado.
- **LLM**: implementado e testado com respostas simuladas. Não foi exercitado contra a API real do Gemini
  (sem chave na sessão de desenvolvimento); o modelo padrão pode precisar de ajuste em `GEMINI_MODEL`.
- **Não verificado em produção**: os workflows do GitHub, o Supabase real (o schema foi validado em um
  PostgreSQL 16 local e a escrita REST com transporte simulado) e o `supabase-js` real (o modo ao vivo foi
  testado com um módulo simulado; a URL `esm.sh` com versão fixada responde). O primeiro run do workflow é
  o teste de verdade.
- **Fontes tipográficas** vêm do Google Fonts; sem rede, o site usa fontes do sistema.
