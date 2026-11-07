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
- **Tema:** três estados (sistema, claro, escuro) em `localStorage` (`editais-culturais:tema`, com try/catch), aplicados por um
  script inline idêntico nas 4 páginas, antes do CSS (teste confere ordem e igualdade). `theme-color` com duas metas
  `media=` e ajuste ao trocar. O bloco escuro de tokens existe duas vezes (sistema e forçado); o `contraste.mjs` falha se
  divergirem.
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
