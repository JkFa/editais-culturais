# DECISIONS

Registro de decisões não óbvias. Formato: data, decisão, motivo, alternativa descartada.

## Fase 0: reconhecimento das fontes (06/11/2026)

A rede da sessão não bloqueou as requisições. Tudo abaixo foi observado em respostas reais,
salvas em `tests/fixtures/`.

### Formato real da API `/api/opportunity/find`

- **Datas** (`registrationFrom`, `registrationTo`, `createTimestamp`, `publishTimestamp`):
  objeto `{"date": "2026-11-06 23:59:00.000000", "timezone_type": 3, "timezone": "America/Fortaleza"}`,
  ou `null`. O fuso **varia por instância** (`Etc/GMT+3` em mapa.cultura.gov.br, `America/Fortaleza`,
  `America/Recife`, `America/Bahia`, `America/Belem`). `Etc/GMT+3` é UTC-3 (sinal invertido pelo POSIX).
- **Campos úteis** (via `@select`): `id`, `name`, `shortDescription`, `longDescription` (texto puro,
  nenhum HTML nas fixtures; ausente em CE e Aracaju), `type.name`, `terms.area` (lista de strings;
  vazia em CE, Aracaju e parte de PE), `singleUrl`, `owner.name` (agente), `ownerEntity.name`
  (órgão/projeto; `null` em várias instâncias), `isContinuousFlow`, `files`, `status`.
- **Link público**: o campo `singleUrl` já traz `https://<instância>/oportunidade/<id>/`. Fallback
  montado igual quando ausente.
- **Área/linguagem**: `terms.area`, lista de strings livres (299 valores distintos nas fixtures, de
  "Música" a "Cultura dos caiçaras"). Não há campo de "linguagem" separado.
- **Filtro de período aberto**: `registrationTo=GTE(AAAA-MM-DD HH:MM:SS)` e
  `registrationFrom=LTE(...)` funcionam; `BETWEEN(a,b)` não funciona (devolve vazio/erro).
  Paginação por `@limit`/`@page` funciona; `@order=registrationTo ASC` também.
- **`@select=files.rules,files.downloads` junto não funciona** (devolve `[]` se não houver `rules`);
  `files.downloads` sozinho funciona. Usamos `files` inteiro (~240 KB para 64 itens) e escolhemos o PDF
  no cliente: `files.rules` se for PDF, senão o primeiro PDF de `files.downloads`.
- `@select=*` retorna erro; `@select` com campos inexistentes devolve `null` no campo.
- `type`: as instâncias misturam editais com cursos, oficinas, eventos, feiras, "Programa" etc.
  Só `type.name == "Edital"` entra (configurável em `fontes.yaml`).
- **Ruído real**: itens de teste ("TESTE EDITAL 0026"), eleições de conselho de cultura e anúncios de
  pessoas físicas aparecem como "Edital". Filtramos por regex de título (`fontes.yaml`); o que passar
  disso é limitação conhecida (README).
- **Fluxo contínuo**: vem com `registrationTo` em 2100/2111. Sem prazo real, não cabe em
  `inscricao_fim not null`; descartados (`ano_sem_prazo_a_partir_de: 2090`). Prazos reais longos
  (ex.: 2028 em Aracaju) entram normalmente.

### Instâncias sondadas

Sondagem de ~70 hostnames prováveis (padrão `mapacultural.<uf>.gov.br` etc.). Responderam JSON:

| instância | situação |
|---|---|
| mapa.cultura.gov.br | ativa, 64 itens abertos (3 são editais após filtro) |
| mapacultural.secult.ce.gov.br | ativa, 33 itens (23 editais) |
| mapacultural.pe.gov.br | ativa, 15 itens (8 editais) |
| mapacultural.aracaju.se.gov.br | ativa, 7 editais |
| mapacultural.se.gov.br | ativa, 2 editais |
| mapacultural.natal.rn.gov.br | ativa, 2 itens (1 "Programa" descartado) |
| mapacultural.pa.gov.br, mapacultural.pb.gov.br, mapacultural.campinas.sp.gov.br | respondem, mas sem edital aberto hoje; mantidas ativas (a API funciona) |
| mapa.cultura.df.gov.br | `ativa: false`: HTTP 403 em `/api/opportunity/find` |
| demais hostnames | sem resposta (DNS/conexão): não entraram no YAML |

### Decisões de implementação

- **Fuso das datas**: respeitamos o fuso que a API informa (e usamos America/Sao_Paulo só quando
  falta ou é inválido). A especificação mandava assumir São Paulo; isso erraria em estados com outro
  fuso (Manaus, Rio Branco). Descartada: forçar São Paulo sempre.
- **"Data sem horário"**: o Mapas sempre grava hora. Tratamos `00:00` e `23:59` como "só o dia"
  (`fim_tem_horario=false`, fim = 23:59:59 local). Risco aceito: um prazo real à meia-noite vira o
  fim do dia. Descartada: tratar 00:00 como horário real (geraria "Encerra hoje às 0h" em editais
  que só informaram a data).
- **Chave de URL do edital**: `?edital=<chave>` onde `chave = fonte:id_externo` (coluna gerada
  `chave`). O `id` do banco não existe no snapshot, e a URL compartilhada precisa funcionar nos dois.
- **`evidencias jsonb` e `proveniencia jsonb`**: uma coluna só, `proveniencia`
  (`{campo: {via, trecho}}`). A especificação citava as duas; o conteúdo seria idêntico.
- **Sem biblioteca `supabase`**: o worker usa PostgREST por HTTP (`httpx`). Menos dependência e
  testável com `MockTransport`.
- **LLM em módulo próprio** (`worker/enriquecimento_llm.py`) em vez de dentro de `padronizacao.py`,
  para manter a padronização determinística e pura. Cache em `worker/cache/llm_cache.json`
  (indexado pelo hash do texto-fonte + modelo + versão do prompt), commitado pelo Action, porque o
  runner é efêmero. Além da regra "o trecho contém o número", exigimos que o trecho exista
  literalmente no texto-fonte.
- **`documentos_exigidos` da curadoria sem evidência**: o YAML entregue não traz evidência para essa
  chave. A especificação proíbe alterar o arquivo. O worker aceita, registra `proveniencia` com
  `via=curadoria` e `trecho=null`, e emite aviso no log. Para valores, proponentes, contrapartida e
  prazo a evidência é obrigatória (a validação rejeita sem ela).
- **Valores em R$, falsos positivos evitados**: ignoramos valores na mesma oração de `taxa`,
  `cobrada`, `parecerista`, `honorários`, `cachê`, `diária` (dinheiro pago pelo proponente ou
  remuneração de serviço, não fomento), e números digitados com espaço no meio (`R$ 14. 440,00`).
  Vários tetos no mesmo edital: usamos o maior. Total menor que teto: descartamos o total.
- **Campos que o regex não deriva** (`exige_contrapartida`, `documentos_exigidos`, `cronograma`,
  `num_cotas`) ficam nulos para fontes Mapas; só a curadoria e o LLM (com evidência) os preenchem.
- **Fonte que falha no dia** mantém no snapshot o que tinha na véspera (ainda aberto), em vez de
  sumir do fallback. Fonte coletada com sucesso remove do banco o que deixou de aparecer enquanto
  ainda estava aberto (edital retirado). Encerrados ficam até `limpar_editais_antigos` (180 dias).
- **`coletado_em`** significa "última vez que o conteúdo mudou": edital com `hash_conteudo` igual
  preserva `coletado_em`/`atualizado_em`, igual no Supabase (que não recebe update de linha idêntica)
  e no snapshot.
- **Código de saída**: 1 se todas as fontes falharem (snapshot anterior preservado); 3 se a coleta
  deu certo mas a gravação no Supabase falhou (o snapshot já foi escrito).
- **Arquivos na raiz fora do escopo**: `PROMPT_editais_culturais.md` e `ANÁLISE_COMPETITIVA.md`
  são idênticos byte a byte (mesmo conteúdo, sem análise competitiva de fato). O segundo foi ignorado.
  `fontes_curadas.yaml` na raiz foi copiado para `worker/` sem alteração (conferido com `cmp`).

## Fase 3: workflows

- **Deploy escuta `workflow_run`** do "Sincronizar editais", além de `push` em `web/**`. Commits feitos
  com `GITHUB_TOKEN` não disparam `push`; sem isso o snapshot novo não seria publicado.
- **Commit do snapshot com `if: always()`**: se o Supabase falhar depois de o snapshot ser escrito (código
  3), o snapshot ainda vai para o repositório. O job continua marcado como falho, para alertar.
- **Trava contra `service_role` no front**: o deploy decodifica a claim `role` da JWT em
  `vars.SUPABASE_ANON_KEY` e aborta se for `service_role`. Descartada: confiar só na documentação.
- **`web/config.js` versionado vazio** (e `config.example.js` documentado). Sem o arquivo, a tag `<script>`
  geraria um 404 no console, e a definição de pronto exige zero erros abrindo sem configuração.

## Fase 4: frontend

- **Contraste (AA)**: `#C84B31` sobre o branco dá 4,66:1, mas sobre o papel `#F7F5F0` dá só 4,28:1. O texto
  de urgência usa `#B5412A` (5,13:1 no papel, 5,59:1 no branco); `#C84B31` ficou só como preenchimento do
  badge "Encerra hoje" (texto branco, 4,66:1). `#2D5A27` dá 7,4:1 no papel. Texto secundário `#5F5C54` (6,1:1).
- **Busca no modo ao vivo**: `textSearch('busca_textual', q, {config:'portuguese', type:'websearch'})` soma
  seus acertos aos da busca local por substring (sem acento). Só a busca textual do Postgres não acharia
  prefixos ("mus" → "música"). Os demais filtros rodam sempre no cliente: a base aberta é pequena.
- **Estado na URL**: filtros usam `replaceState`; abrir o painel usa `pushState`, para que o botão voltar
  feche o painel (`?edital=<chave>`). O popstate só redesenha a lista se os filtros mudaram, senão o foco
  não voltaria ao card (bug achado no teste ponta a ponta).
- **Perfil**: seleção única (clicar de novo desmarca). Linguagem: múltipla (OU). "Nacional" e UF são
  exclusivos entre si; UF mostra a UF e os nacionais.
- **CSV com `;`** e BOM: é o separador que o Excel pt-BR espera. Células iniciadas por `= + - @` levam
  apóstrofo (injeção de fórmula a partir de títulos públicos).
- **`.ics`**: evento de dia inteiro quando o prazo não tem horário; 30 min a partir do horário quando tem.
  Linhas dobradas em 75 octetos e lembrete (`VALARM`) de um dia antes.
- **Favoritos guardam um mínimo** (título, órgão, link, prazo) para listar, como "Encerrado ou removido",
  o edital que saiu da base, sem inventar nada.
- **Radar** vem no snapshot (`radar`) e do Supabase (`radar_editais`); a ordem é cíclica pela distância
  em meses até a janela (0 = dentro dela).

## Fase 5: verificação

Ver o README ("Testes e verificações") e a seção "Limitações conhecidas". Resultado da execução final está
no resumo entregue ao fim do trabalho.

## Deploy (07/11/2026)

- **Organização do Supabase:** `veleiro-survey` (sem projetos), escolhida por não arriscar o limite de 2 projetos ativos
  do plano gratuito (`Adapta` já estava ativo; os outros 3 estavam pausados).
- **Chaves:** o projeto tem as chaves legadas (JWT) e as novas. A API de chaves devolve a `sb_secret_…` **mascarada**
  (com `•`), então o primeiro run falhou com `UnicodeEncodeError` ao montar o cabeçalho. Usamos a `service_role` legada
  como secret e a `sb_publishable_…` como variable (testada: leitura 200; POST, RPC e `heartbeat` negados com 401/42501
  para a publishable e a anon). `persistencia.py` já aceita `sb_secret_` (só em `apikey`, sem `Authorization`) para quando
  a pessoa cadastrar a secret key à mão; o deploy também aborta se `SUPABASE_ANON_KEY` for uma `sb_secret_`.
- **Bug achado no primeiro run:** o worker decidia pular o upsert comparando com o *snapshot* anterior; com o banco vazio,
  gravou 0 linhas. Agora compara só com o banco (o snapshot só serve para preservar `coletado_em`). Teste de regressão incluído.
  Conteúdo vindo do snapshot de uma fonte que falhou também é gravado no banco, para modo ao vivo e fallback ficarem iguais.
- **ConnectTimeout** de PE, SE, Campinas e às vezes PB nos runners do GitHub: não resolvido (bloqueio de rede do lado das
  instâncias). Timeout subiu para 60 s e o log passou a registrar o tipo da exceção (antes a mensagem vinha vazia).
- Erro meu no processo: a varredura de segredos no histórico e o `gh repo create --push` rodaram no mesmo comando, sem esperar
  o resultado da varredura antes do push. O resultado foi limpo (só a string fictícia `sb_secret_de-teste` de um teste).

## v2: home, visitas, tema claro/escuro, celular (07/11/2026)

- **Rotas:** `/` é a home; o explorador foi para `/editais/` (`web/editais/index.html`), com `sobre/` e `404.html`.
  Um script inline no topo da home (antes do CSS e do `<body>`) redireciona qualquer link antigo para `editais/` com os mesmos
  parâmetros. A lista cobre os que o explorador usa de fato (`q`, `uf`, `perfil`, `ling`, `abr`, `abertas`, `ord`, `edital`, `aba`)
  e os apelidos do pedido (`ordem` → `ord`, `abertos` → `abertas`). Testado um a um e com o link real `?uf=CE&edital=…`.
  O `PROMPT.md` citado no pedido não existe; li `PROMPT_editais_culturais.md`.
- **404 com `<base href="/editais-culturais/">`:** o Pages serve a 404 em qualquer profundidade, e caminho relativo quebraria.
  Se o repositório for renomeado, é preciso trocar esse valor.
- **`robots.txt` e `sitemap.xml` ficam em `/editais-culturais/`:** crawlers só leem `robots.txt` na raiz do host
  (`jkfa.github.io/robots.txt`), que um project site não controla. Os arquivos existem como pedido; o `<link rel=canonical>`
  e o sitemap ajudam, o `robots.txt` é inócuo. Domínio próprio resolveria.
- **Fontes hospedadas no próprio site** (`web/assets/fonts`, subconjunto latin, OFL; `fontes.css`), em vez do `preconnect` ao
  Google Fonts pedido. Motivo: o portal promete "sem rastreio"; a requisição ao Google entregava o IP de cada visitante, e a
  troca de fonte pesava no Lighthouse (home 80 → 94 em performance). Custo: ~100 KB no repositório.
  O `→` de `.link-seta` cai na fonte do sistema (U+2192 fora do subconjunto).
- **`supabase-js` removido do site:** os dados ao vivo e a busca textual usam o PostgREST por `fetch`
  (`wfts(portuguese).termo`). Menos 100 KB de CDN e uma dependência a menos. Descartada: carregar supabase-js sob demanda.
- **Dados em duas etapas:** a página mostra o `snapshot.json` (cache normal do Pages) e troca pelos dados ao vivo quando chegam;
  o rodapé diz "Atualizando…" nesse intervalo. "Última atualização" vem sempre do `gerado_em` do snapshot.
- **"Fontes consultadas" na home** é "fontes com editais abertos" (7 hoje): a contagem exata de fontes consultadas só existe no
  snapshot, e a do banco ao vivo seria outra. Preferi um número que vale nos dois modos.
- **Valor total na home e no cabeçalho do explorador** em formato compacto ("R$ 5,2 mi", valor exato no `title`): o valor
  cheio quebrava a linha em 4 colunas e estourava em 360 px.
- **Tema (revisto em 07/11/2026):** o site **abre sempre no claro**, sem consultar `prefers-color-scheme`; o escuro é opcional,
  pelo botão do cabeçalho. Só `escuro` vai para o `localStorage` (`editais-culturais:tema`, com try/catch); qualquer outro valor
  guardado antes ("sistema", "claro") abre no claro. Script inline idêntico nas 5 páginas, antes do CSS (teste confere ordem e
  igualdade). Uma única meta `theme-color`, ajustada ao trocar. Antes eram três estados (sistema, claro, escuro); removi o
  "sistema" a pedido, o que também eliminou o bloco duplicado de tokens escuros.
- **Contraste (valores finais, `scripts/contraste.mjs`, WCAG AA):** claro: fundo `#F7F5F0`, superfície `#FFFFFF`, texto `#141413`,
  texto suave `#5C5A54` (6,3:1), borda decorativa `#E2DFD7`, **borda de controle `#7D796C`** (≥3:1; a decorativa não passa),
  urgente `#B03F27` (5,4:1 no fundo; o `#C84B31` dá 4,3:1, então só preenche o badge "hoje" com texto branco 4,7:1), aberto
  `#2D5A27`. Escuro: fundo `#161513`, superfície `#1E1D1A`, elevada `#262420`, texto `#ECE8DF`, suave `#A8A398` (7,3:1), borda
  `#34322C`, borda de controle `#7A766B`, urgente `#E07A5F` (6,2:1), aberto `#8DB580`, foco `#ECE8DF`. Badges no escuro: fundo
  translúcido (12%) com texto no acento. Cartograma: 6 níveis por tema com texto próprio (≥4,5:1); o nível 0 usa borda tracejada.
- **Visitas (LGPD):** a function recebe só `{pagina}` (`text/plain`, sem preflight, `keepalive`, sem cookies nem referrer); uma
  visita por aba e dia (`sessionStorage`); `navigator.webdriver` e user-agent de bot não contam; DNT e Sec-GPC são lidos **no
  servidor** (o navegador os envia sozinho) e a visita é contada com localização `ZZ`. Rate limit de 3 por minuto por IP em
  memória do isolate (não é global entre instâncias: contém abuso simples, não ataque distribuído). A tabela só tem
  `dia, pagina, pais, uf, cidade, total`; `ZZ`/`''` = desconhecido. Fora do Brasil só se guarda o país. Cidade com menos de 5
  visitas em 30 dias vira "outras cidades" dentro de `visitas_resumo()` (`security definer`; o anon não lê a tabela).
  `verify_jwt = false` na function: as chaves `sb_publishable_` não são JWT e a function é pública por desenho.
- **Provedor de geolocalização: ipwho.is.** Sem chave, HTTPS, uso comercial permitido, 1.000 req/dia por IP de origem
  (docs em ipwhois.io/documentation). Descartados: ip-api.com (HTTP no plano grátis, uso não comercial) e ipinfo/MaxMind (exigem
  conta e chave). Limite real: o IP de origem é o da Edge Function (compartilhado), então o teto de 1.000/dia pode estourar em
  dias de pico; nesse caso a visita conta com localização desconhecida, sem erro. O provedor recebe o IP naquela requisição
  (declarado em `sobre/#privacidade`). Alternativa se faltar cota: base DB-IP Lite local (CC BY 4.0).
- **Teste da function:** a lógica pura está em `logica.mjs` (`node --test`, 7 casos). A integração roda no Deno oficial via Docker
  (`scripts/testar_registrar_visita.sh`), porque `supabase functions serve` exige `supabase start` (stack completo).
  Verificado: bot e página inválida não geram registro; DNT/GPC gravam `ZZ`; provedor inacessível ou IP de documentação grava
  `ZZ`; 4º pedido no minuto dá 429; **o IP não aparece no log da function nem no que ela grava**.
- **Celular:** a barra de filtros vira folha inferior abaixo de 48 rem (`role=dialog` só nesse modo, foco preso, Esc, foco volta
  ao botão), botão fixo "Filtros (n)" (a busca não conta), painel do edital em tela cheia com o botão do WhatsApp em rodapé fixo
  (`env(safe-area-inset-bottom)`), alvos ≥ 44 px (testado nas 4 páginas), inputs ≥ 16 px, chips com `scroll-snap`. O card inteiro
  é a área de toque via `::after` do título. A barra de fonte dos dados deixa de ser fixa no celular (o espaço é do botão).
- **Lighthouse mobile** (servidor local, relatórios em `docs/lighthouse/`): home 94/100/100/100, explorador 92/100/100/100
  (performance/acessibilidade/boas práticas/SEO). CLS ~0,1 por conteúdo que chega depois do snapshot; reservei altura da lista e
  da semana. O número varia entre execuções.
- **PWA leve:** só `manifest.webmanifest` e ícones; nenhum service worker (evita mostrar edital vencido). Imagem OG: SVG em
  `assets/img/og.svg` convertido para PNG por `scripts/gerar_imagens.py` (Playwright, já dependência de teste).
- **Testes:** fixtures de e2e foram para o `conftest.py` e os auxiliares para `tests/e2e_util.py`; `test_v2_site.py` cobre
  redirecionamentos, tema sem flash, visitas, cartograma e celular × temas (360×740 e 390×844); screenshots em `docs/v2/`.

## v2 em produção (07/11/2026)

- Migration e function aplicadas pela CLI (`supabase db push`, `functions deploy --use-api --no-verify-jwt`, sem Docker); o
  painel foi usado para conferência. Nenhuma chave passou pela tela.
- Primeiro teste em produção parecia não contar: o Chromium headless do Playwright se anuncia como `HeadlessChrome`, que o filtro
  de bots da function barra. É o comportamento desejado; o teste passou com user-agent comum (+1 por aba, 0 no reload).
- Bug de texto achado em produção: com 1 visita a frase dizia "1 visitas". Corrigido no singular, com teste.

## v4: novas fontes (07/11/2026)

Pré-requisito do pedido: a "fila de curadoria" e a tabela `fontes_status` da v3 não existiam (o
`PROMPT_v3_evolucao_completa.md` não está no repositório). Implementei a versão mínima: Issues com as
etiquetas `candidato` e `aprovado` (`worker/fila.py`) e `fontes_status` (migration
`20261107200000_v4_fontes.sql`).

### Modelo

- Catálogo em `worker/fontes.yaml` (`fontes:` + as `instancias:` de Mapas Culturais). Tipos:
  **oficial** e **plataforma** publicam com evidência; **descoberta** nunca publica, só abre candidato.
- Candidato = Issue `candidato` com marcador `<!-- candidato:<hash> -->`, link oficial, trecho lido da
  página oficial e um YAML proposto. A pessoa completa o YAML (evidência obrigatória), troca a etiqueta
  por `aprovado` e o sync seguinte publica (fonte `curadoria`, id `fila-<n>`); prazo vencido fecha a Issue;
  YAML inválido recebe comentário. Nada é recriado, mesmo com a Issue fechada.
- **Prazo nunca é adivinhado:** `worker/prazos_texto.py` só aceita data com ano e guarda o trecho. Edital sem
  prazo legível vira candidato, não edital (foi o caso de 2 dos 4 editais do MinC).
- **Cliente web educado** (`worker/web.py`): robots.txt (e Crawl-delay), User-Agent com o link do repositório,
  sequencial por domínio com intervalo, timeout de 30 s. 403/429 ou desafio anti-bot viram `bloqueada_em_ci`
  e **não são contornados** (uma tentativa, sem retry, sem outro User-Agent).
- **Cache:** hash do texto principal nos vigias (`worker/cache/vigias.json`); ETag suportado no cliente, mas
  nenhuma das fontes atuais devolve ETag nas listagens, então não é usado em coleta (honesto: o pedido de
  "ETag ou hash" fica coberto só pelo hash).
- **Deduplicação** (`worker/dedup.py`): mesma URL normalizada em qualquer par de fontes, ou prazo no mesmo dia
  + títulos parecidos (Jaccard das palavras de conteúdo ≥ 0,4, ≥ 3 em comum) **só entre fontes diferentes**.
  Duas ofertas da mesma fonte nunca se juntam (os 5 "Funarte Aberta" têm título e prazo quase iguais).
  Canônico: curadoria > oficial > plataforma > descoberta; as outras origens vão em `fontes_alternativas`.
  Primeira versão juntava demais (rodei no dado real e vi falsos positivos entre editais PNAB de municípios
  diferentes); corrigi antes de publicar.
- **Tipo de apoio** (5 valores) e **requisitos** ("Exige projeto aprovado na Lei Rouanet") só com evidência regex
  no texto, com negação ignorada (o texto real do Rouanet nas Favelas 2 *veda* projetos com Pronac ativo, e o
  regex não vira exigência). Sem evidência = `null`; sob o filtro "Tipo de apoio" o `null` some.
- **Regex de valores:** "R$ 1.000.000 para cada uma das localidades" (garantia por local) e "montante mínimo de"
  (piso) deixaram de contar como teto/total; achado no texto real do MinC.
- `--fonte <id|todas> [--dry-run]` roda fontes isoladas no Actions sem mexer no snapshot.

### Fontes avaliadas (07/11/2026)

| fonte | tipo | método | status | motivo / observação |
|---|---|---|---|---|
| minc-inscricoes-abertas | oficial | html (Plone) | **ativa** | sem REST nem RSS (404); `a.govbr-card-content` + página de cada edital; a lista "abertas" traz editais já encerrados |
| cultbr-pnab | plataforma | api | **ativa** | API pública do portal; 12 editais estruturados (prazo, valor, proponentes) + candidatos; "via CultBR (MinC)" |
| radar-clube-emma | descoberta | html | **ativa** | só links; 20 candidatos/dia; ignora Prosas e CultBR |
| mapas:es, mapas:cim-amfri, mapas:parauapebas | plataforma | api | **ativas** | descobertas pelos links do Radar; a API de Mapas respondeu |
| vigia-petrobras-cultural | oficial | vigia | **ativa** | |
| vigia-sesi-sp-editais | oficial | vigia | **ativa** | |
| vigia-sesc-pr-editais | oficial | vigia | ativa, **bloqueada_em_ci** | HTTP 403 só no runner do GitHub (da minha máquina responde). Não contornamos; Issue semanal "Conferir manualmente" |
| vigia-sesc-rj-pulsar | oficial | vigia | **ativa** | |
| vigia-instituto-cultural-vale | oficial | vigia | **ativa** | |
| vigia-spcine-fomento | oficial | vigia | **ativa** | |
| vigia-funarte-editais | oficial | vigia | **ativa** | |
| vigia-riofilme-chamadas | oficial | vigia | **ativa** | |
| vigia-rj-incentivo-cultura | oficial | vigia | **ativa** | |
| vigia-claro-patrocinios | oficial | vigia | **ativa** | patrocínio contínuo, link vindo do Radar |
| vigia-renner-patrocinios | oficial | vigia | **ativa** | idem |
| prosas-central-de-editais | plataforma | html | **desativada** | termos proíbem extração de dados; Crawl-delay 30 s (ver abaixo) |
| culteditais | oficial | html | **desativada** | desafio anti-bot do Cloudflare; dados chegam pelo CultBR |
| shell-premio-teatro | oficial | html | desativada | sem inscrição pública; só Destaque Nacional exige formulário (radar) |
| bb-patrocinios, caixa-patrocinios, bndes-patrocinios, porto-seguro-patrocinio, smc-sp-editais | oficial | vigia | desativadas | páginas em JavaScript: 18 a 150 caracteres de texto para o robô |
| natura-musical, santander-patrocinios | oficial | vigia | desativadas | HTTP 403 para o robô |
| ancine-fsa | oficial | vigia | desativada | URL estável não encontrada (timeout / 404) |
| descentra-mg | plataforma | html | desativada | exige login; editais PNAB de MG que passam pelo CultBR entram por lá |
| itau-cultural | oficial | vigia | desativada | sem página estável só de editais; a home geraria falso alarme |
| oi-futuro | oficial | vigia | desativada | ConnectTimeout |
| sesc-sp/df/mg/pe/go, sesc-sc, sesc-se/pb, secult-es | — | — | descartadas | URLs de edital que chutei deram 404 ou erro de DNS; não entraram no catálogo |

Total: **17 fontes novas ativas** (3 instâncias de Mapas + MinC + CultBR + Radar + 11 vigias), 16 saudáveis no
GitHub Actions e 1 bloqueada. O pedido sugeria 15.

### Termos de uso conferidos (07/11/2026)

- **Prosas:** `https://produtos.prosas.com.br/termos-de-uso` veda "usar quaisquer métodos de prospecção ou métodos
  semelhantes de extração de dados"; `https://prosas.com.br/robots.txt` tem `Crawl-Delay: 30`. Decisão: **não
  coletar**, nem como `descoberta` (extrair título e link da lista também é extração). O Radar tem 42 links do
  Prosas e o worker os descarta sem abrir. Parceria seria o caminho; os editais do Prosas que importam entram pela
  curadoria, com o link da página de inscrição.
- **Radar de Editais (Clube Emma):** `https://radareditais.clubemma.com.br/robots.txt` libera a home (bloqueia
  só `/fila-revisao-*` e `/candidatos-*`, que não tocamos) e `/termos` diz que as informações são públicas.
  Permitido, como `descoberta`: lemos só os links; títulos, prazos e comentários do código-fonte da página deles
  nunca entram no repositório (o fixture de teste omite os títulos). **Sugestão ao dono:** vale propor uma
  parceria ao Clube Emma (atribuição mútua e um feed de links), já que dependemos da página deles.
- **CultBR (MinC):** termos em `https://portal.cultbr.cultura.gov.br/oportunidades` (rodapé "Termos de uso"):
  permitem consultar e reproduzir informação pública citando a fonte; proíbem só "extração automatizada abusiva".
  Por isso 1 requisição a cada 3 s e a fonte é citada ("via CultBR (MinC)").
- **MinC (gov.br/cultura):** robots.txt permite; sem termos específicos de raspagem.
- **CultEditais:** desafio Cloudflare; não contornamos.
- **Shell:** a página só mostra conteúdo com JavaScript (li pelo navegador). Resposta da pergunta 3 do FAQ: "Espetáculos
  inéditos [...] no Rio de Janeiro e em São Paulo [...] são acompanhados pelo júri"; "Para concorrer nesta categoria
  [Destaque Nacional], é necessário realizar inscrição por meio do formulário que será disponibilizado neste site a
  partir de agosto". Conclusão: **não é edital**; entrou no `radar` com essa explicação.

### Verificação

- Coletores rodados no GitHub Actions (`--fonte todas --dry-run`, run 42370759998): 16 ok, `vigia-sesc-pr-editais`
  `bloqueada_em_ci`. MinC, CultBR e os Mapas novos funcionam do runner.
- Deduplicação em dado real: Ciclo de Saberes (MinC × Mapa da Cultura, prazo igual) vira 1 registro com a
  alternativa; os 5 Funarte Aberta e fomentos PNAB de PE/CE com mesmo prazo **não** se juntam; 10 itens duplicados
  dentro do CultBR são descartados por URL. Não há caso "MinC + Mapa + Prosas" porque o Prosas está desativado.
- Candidatos conferidos no dado real: título, trecho e prazo vêm da página oficial; nenhum texto do Radar. Candidato
  com prazo já vencido é descartado; título de página com template (`{{seal.name}}`) cai para o `<title>`.
- **Produção (07/11/2026, depois do merge do PR #3 e da migration `20261107200000_v4_fontes.sql`):**
  - 1º sync falhou com `KeyError` em `vigia-sesi-sp-editais` (laço de remoção assumia que toda fonte v4 gera
    editais) e as áreas do CultBR em texto viravam letras soltas; corrigidos no PR #39. 2º sync: sucesso em
    12 min 20 s; 3º (após o PR #60): sucesso em 14 min 20 s. Meta era < 20 min.
  - Banco: 65 editais, 42 linhas em `fontes_status`, 59 editais com `via`, 1 com `fontes_alternativas`, 11 MB no total.
  - Fila: 38 candidatos no 1º sync (2 eram ruído: portal de transparência e página "Carregando..."; fechados, e o
    coletor passou a descartar páginas sem menção a edital). O teto é de 20 candidatos novos por execução.
  - Verificado no navegador em produção: filtro "Tipo de apoio", card único do Ciclo de Saberes com "Também
    publicado em: Mapas Culturais (BR)", "Fonte: Município de Campos dos Goytacazes · via CultBR (MinC)" e a lista
    de fontes em `/sobre/`. O resumo do MinC trazia a barra de compartilhar do gov.br; corrigido no PR #60.
  - Limites conhecidos: `mapas:pe`, `mapas:se` e `mapas:campinas` dão `ConnectTimeout` a partir do runner desde o
    v1 (`mapas:pb` falhou uma vez); `vigia-sesc-pr-editais` leva 403 só no runner. A "última coleta boa" dessas
    fontes veio de um dry-run local (data real, mas não do Actions). Nenhuma exigência de projeto aprovado (Rouanet)
    aparece hoje nos abertos: o requisito só existe nos fixtures.


## Assistente de Projetos Culturais (Fase 1)

Escrita guiada por conversa em `/projeto/`. Visão geral e operação em `docs/assistente.md`. Decisões onde o prompt
original conflitava com o repositório (aprovadas pelo usuário em 07/11/2026):

- **Rota.** O GitHub Pages não reescreve rotas; `/projeto/novo?edital=<id>` virou `/projeto/?novo=1&edital=<chave>`
  (uma página com visões por query string). O edital é identificado pela `chave` (`fonte:id_externo`), como no resto do site.
- **`edital_id` é `bigint`, não `uuid`,** porque `editais.id` é `bigint identity`. Como os editais são apagados depois
  de vencer, o projeto guarda também `edital_chave`, `edital_titulo` e `edital_url` (FK com `on delete set null`).
- **Specs.** Fonte única em `specs/`; o Pages só publica `web/` e a Edge Function só empacota a própria pasta, então
  `scripts/sincronizar_specs.mjs` gera as cópias e o CI (`--check` + pytest) falha se saírem de sincronia.
- **TypeScript com Deno no CI** (`denoland/setup-deno`) para `llm.ts`/`prompts.ts` e demais módulos da função: 53 testes
  sem rede. A lógica compartilhada com o navegador (`specs/lib/secoes.mjs`) é JavaScript puro.
- **Dados pessoais em tabela própria** (`projeto_proponente`), que a função nunca lê: a regra de LGPD vale pela
  estrutura do banco, não só pelo código. O texto livre ainda passa por filtro (CPF, CNPJ, e-mail, telefone, CEP).
  O filtro não reconhece nomes de pessoas; a interface avisa.
- **Cotas/pontuação extra são dado sensível:** pergunta com `canal: "formulario"` (não passa pelo chat nem pela IA), fica
  na ficha e só vai a prompt se a pessoa marcar que pode citar. A pergunta de edital também é de formulário.
- **RLS nas tabelas-filhas** por dono do projeto (não há `user_id` nelas). O usuário só **lê** mensagens, respostas,
  histórico e uso; quem escreve é a função (`service_role`). `projeto_secoes` e `projetos` o usuário edita.
- **Histórico de versões** por trigger `security definer` (o usuário só lê o histórico). Editar o texto tira a aprovação.
  Um teste de RLS encontrou o defeito original: o trigger rodava com a permissão do usuário e falhava.
- **`verify_jwt = false`** na função: o preflight CORS não leva JWT. A função valida o token no GoTrue e o dono do
  projeto a cada chamada (leitura com o JWT do usuário, então a RLS filtra; escrita com `service_role`).
- **Contrato do LLM.** O provedor recebe `fatos_novos_ficha` como lista de pares chave/valor (alguns provedores não
  aceitam objeto sem propriedades fixas) e a função devolve o objeto do contrato. Tudo é revalidado; inválido → uma nova
  tentativa → erro tratado (502).
- **Limite diário** atômico na RPC `assistente_consumir_uso` (dia de Brasília). Conta ao iniciar a chamada, inclusive se
  a IA falhar depois (não há estorno nesta fase).
- **Modelo.** `gemini-3.6-flash` (Flash estável na documentação oficial em 07/11/2026; `gemini-2.5-flash` está
  obsoleto). Configurável por `LLM_MODEL`. Groq fica como stub documentado.
- **Plano gratuito do Gemini e LGPD.** Os termos da Google permitem usar prompts e respostas do plano gratuito para
  melhorar produtos e revisão humana. Mitigação: filtro de dados pessoais, ficha restrita, aviso no chat e em `/sobre/`.
  Quem quiser eliminar o risco usa um projeto com faturamento ou outro provedor.
- **`/sobre/` ganhou a seção "Assistente de projetos"** e a frase "o portal não tem login" foi corrigida (navegar segue
  sem login e sem cookies; a sessão do assistente fica em `localStorage`). É a única alteração em página existente além
  do botão no painel do edital.
- **Seções 6 a 11** existem só como placeholders `ativo: false` ("em breve").

### Assistente: verificação em produção (07/11/2026)

- **Publicação:** migração `20261108000000_assistente.sql` aplicada (8 tabelas com RLS; `anon` recebe 401 em todas e na RPC
  do limite), função `assistente` v1 com `verify_jwt = false`, segredos `GEMINI_API_KEY`, `LLM_PROVIDER`, `LLM_MODEL=gemini-3.6-flash`
  e `LIMITE_DIARIO=60`. Auth: só `site_url` e as URLs de redirecionamento foram enviados.
- **`supabase config push` não deve ser usado às cegas:** o `config.toml` do repositório divergia da produção
  (confirmação de e-mail, intervalo mínimo de 1 min entre e-mails, OTP de 8 dígitos e MFA TOTP). O arquivo foi alinhado
  à produção antes do push, e o diff final continha só `site_url` e `additional_redirect_urls`. Rode `echo n | supabase config push`
  para ver o diff antes de qualquer envio.
- **Gemini real:** o modelo `gemini-3.6-flash` e o `responseSchema` (com `fatos_novos_ficha` como lista de pares) foram aceitos.
  Respostas: perguntas adaptadas ao edital; rascunho só com fatos informados e `[COMPLETAR: ...]` onde faltou (4 pontos num
  teste com 3 dados informados); filtro de LGPD tirou o e-mail do texto antes de IA e banco (conferido na tabela `mensagens`).
- **Fluxos conferidos no navegador:** login (link de confirmação do primeiro acesso), botão no painel do edital, criar projeto
  vinculado, chat, rascunho, aprovação com pontos pendentes, edição que tira a aprovação e grava a versão anterior, limite diário
  (429 com mensagem amigável; `LIMITE_DIARIO` voltou para 60) e exclusão em cascata (todas as tabelas ficaram zeradas).
- **`.docx`:** gerado com a biblioteca real (`docx@9.5.0` do jsDelivr) em Deno com `--unsafe-proto` (restrição do Deno, não do
  navegador): arquivo válido, capa com proponente, edital e linguagem, seções em ordem e só os `[COMPLETAR]` em amarelo.
- **Defeitos achados só em produção e corrigidos (PRs #83, #84, #85):** a palavra "null" escrita na tela (`replaceChildren`
  com `null`), a linha "Edital:" vazia (mesmo nó de link em dois lugares) e o contador da lista desatualizado ao excluir. Cada um
  ganhou teste de regressão que falha no código antigo.
- **E-mail do login:** o SMTP padrão do Supabase só entrega para membros do projeto. Resend foi avaliado e **não resolve sem
  domínio próprio verificado** (a conta não tem domínio; sem ele só envia para o e-mail do dono). Para o público geral entrar é
  preciso um domínio com DNS (Resend, Brevo ou outro) e configurar *Authentication → SMTP Settings*.
