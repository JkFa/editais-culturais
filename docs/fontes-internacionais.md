# Fontes internacionais (Fase 1)

Oportunidades de fora do Brasil que um artista que mora aqui pode, de fato, disputar: residências, mobilidade,
prêmios e fomento. Ficam no filtro **Internacional** do portal (`/editais/?amb=internacional`) e **não entram** nos
números dos editais brasileiros (a home tem um contador próprio).

Princípios (os mesmos do resto do portal): custo zero, nada de LLM nesta fase, nada de inventar dado. Só mostramos
título, um trecho curto, prazo, tipo e link para o original, sempre com a fonte à vista. **Nunca** copiamos o texto
integral de uma chamada.

## Como o dado é guardado

Colunas novas em `public.editais` (migration `20261109000000_internacional.sql`, também em `supabase/schema.sql`).
`abrangencia` (nacional/estadual/municipal) continua só para editais do Brasil; o alcance de fora vai em `ambito`.

| Campo | Valores | Observação |
|---|---|---|
| `ambito` | `brasil` (padrão) · `internacional` · `estrangeiro_nacional` | Editais atuais: `brasil` |
| `pais_destino` | ISO-3166 alfa-2, vários separados por vírgula (`BE,HR,DE`), ou `Ibero-América`, `Europa`, `Online` | Onde a atividade acontece |
| `elegibilidade_br` | `aberto` · `requer_residencia` · `requer_parceiro` · `verificar` (padrão) | Ver regras abaixo |
| `tipo_oportunidade` | `residencia` · `mobilidade` · `projeto` · `premio` · `formacao` · `apresentacao` · `outro` | |
| `custos_cobertos` | `viagem`, `hospedagem`, `cache`, `producao`, `alimentacao`, `acessibilidade` | Só o que a fonte escreve |
| `idioma_original` | `pt`, `es`, `en`, `fr`… | |
| `moeda`, `valor_original` | texto, numérico | Sem conversão |
| `valor_brl` | numérico | **Vazio** (Fase 2: câmbio) |
| `prazo_original`, `prazo_fuso` | timestamp, `Europe/Brussels` etc. | Como a fonte informa |
| `titulo_original` | texto | Título no idioma da fonte |
| `resumo_pt` | texto | **Vazio** (Fase 2: tradução) |

O prazo que o portal já usa (`inscricao_fim`) continua preenchido para "Encerram esta semana" e os contadores
funcionarem. Prazo só com data vale até 23:59:59 de Brasília (padrão do portal) e o card avisa para conferir o
horário na chamada. Chamada **sem prazo comprovável não é publicada** (a coluna é obrigatória e o portal não
chuta); ela é contada em `descartes.sem_prazo`.

O hash de conteúdo dos editais brasileiros **não muda** (os campos novos só entram no hash quando há dado
internacional), então a primeira coleta depois da migration não regrava nem altera o "coletado em" de nenhum edital.

## Regras de elegibilidade (`worker/elegibilidade.py`)

Lê o texto da fonte frase a frase (inglês, espanhol e português).

- **`aberto`**: só com frase explícita: qualquer nacionalidade, "de todos os países", América Latina, Brasil
  (ex.: "applicants must have been trained in Africa, Asia, Latin America…"). A frase fica em
  `proveniencia.elegibilidade_br.trecho` e aparece no painel.
- **`requer_residencia`**: "must reside/be resident of…", "debe residir…", "deve residir…".
- **`requer_parceiro`**: exigência escrita de parceiro/consórcio (ex.: "must be submitted by a consortium").
  "Instituições parceiras" que apenas hospedam o programa **não** contam.
- **`verificar`**: tudo o mais, qualquer negação ("not open to…") e qualquer sinal contraditório
  ("open to all nationalities residing in Europe").
- **Custos**: a frase precisa ter verbo de cobertura ("covers", "cubre", "inclui") e item, sem negação
  ("not", "separately", "arranged by", "por conta de").

Testes: `tests/test_internacional_elegibilidade.py` e `tests/test_internacional_onthemove.py`.

## Fontes

### On the Move (ativa) · `coletor: onthemove`

- Site: https://on-the-move.org · método: **RSS** · uma coleta por dia · idioma: inglês (sem versão em português).
- Em `/news` há o link "Subscribe to an RSS feed for this search". O caminho do feed tem seis facetas:
  `news/<tipo>/<país>/<online>/<perfil>/<disciplina>/<tema>/feed` (cada uma é um id ou `all`).
  Feed de todas as chamadas abertas: `/news/all/all/all/all/all/all/feed`. Traz título, texto, link da chamada
  original, link de inscrição e `Deadline: <time datetime=…>`. **Não** traz tipo nem disciplina.
- Para o tipo e a disciplina lemos um feed por faceta e cruzamos pelo link: 7 tipos + 9 disciplinas + "Online"
  (~18 requisições por dia, 3 s entre elas).

  | Tipo (facetas 1) | `tipo_oportunidade` |
  |---|---|
  | 7 Residencies | `residencia` |
  | 3 Project Funding | `projeto` |
  | 2 Competitions & Awards | `premio` |
  | 4 Presenting Work | `apresentacao` |
  | 81 Fellowships · 8 Training | `formacao` |
  | 6 Meetings & Collaboration | `outro` |

  Disciplinas (faceta 5) → linguagens: Performing Arts → Teatro & Dança · Music & Sound → Música · Film → Cinema &
  Audiovisual · Literature → Literatura · Cultural Heritage → Patrimônio · Visual Arts & Design → Artes Visuais ·
  Cross-disciplinary, Architecture, Digital/New Media → Multiáreas. "Multiáreas" some quando há linguagem específica.
- País: o que está entre parênteses no fim do título (`(Belgium, Croatia, Germany)` → `BE,HR,DE`); "Online" só
  quando não há país e a chamada está na faceta "Online or Remote".
- `robots.txt` (07/11/2026): só bloqueia `?f[…]`, `/search/`, `/user/…` etc.; os caminhos de feed são permitidos.
  O site **não publica termos de reutilização do conteúdo** (só a política de privacidade); usamos o feed que ele
  mesmo oferece ao público, sem o texto integral, sempre com "Fonte: On the Move" e link.
- Se uma faceta falhar, a coleta segue sem aquele dado (tipo/linguagem vazios). Se o feed principal falhar,
  só essa fonte vira `erro` em `fontes_status`; as demais continuam.

### IberCultura Viva (ativa) · instância de Mapas Culturais

- Os editais do programa ficam em https://mapa.iberculturaviva.org (a mesma API `/api/opportunity/find` das
  instâncias brasileiras). Entra como `mapas:iberculturaviva` com `ambito: internacional`, `pais_destino:
  Ibero-América`, idioma espanhol. Só o tipo "Edital" com prazo final definido.
- `robots.txt` do mapa: sem regras (apenas avisos de "content signals"); o do portal institucional pede 60 s entre
  requisições, que não usamos (só consultamos a API do mapa, uma vez por dia).
- Hoje (07/11/2026) **não há edital aberto**. A última chamada de mobilidade foi em janeiro de 2026.
- `elegibilidade_br`: `aberto` só se o texto da chamada listar o Brasil entre os países elegíveis; senão `verificar`.

### Ibermúsicas, Ibermedia, Iberescena, Ibermuseos (inativas, só curadoria)

| Programa | Por que não há coletor | Como entra |
|---|---|---|
| Ibermúsicas (`ibermusicas.org`) | A cadeia de certificados TLS do site está incompleta: o navegador abre, mas o robô e o Actions recusam, e não desligamos a verificação. As convocatórias 2026 fecharam em 02/10/2026. | Curadoria quando houver chamada aberta |
| Ibermedia (`programaibermedia.com`) | Convocatória anual numa página única de texto livre; a de 2026 encerrou em 30/03/2026. (`ibermedia.com` é outro site, de software.) | Curadoria |
| Iberescena (`iberescena.org`) | Site montado por JavaScript; os dados vêm de um CMS cuja API responde 403 sem autenticação. Sem RSS. | Curadoria |
| Ibermuseos (`ibermuseos.org`) | O RSS é de notícias; a lista de convocatórias não traz prazo (ele só aparece em texto corrido numa notícia). | Curadoria. O 7º Fundo Ibermuseos (prazo 05/03/2027) já está cadastrado |

O prompt original chamava o terceiro programa de "Ibercena" e pedia um adaptador HTML para Ibermuseos; o nome
oficial é **Iberescena**, e o adaptador de Ibermuseos ficou na curadoria pelo motivo da tabela.

### Endereços avaliados em 08/11/2026

Seis endereços foram abertos e conferidos antes de qualquer cadastro. Regra: só entra chamada aberta, com prazo futuro e evidência;
agregadores só servem para descobrir.

| Endereço | Resultado |
|---|---|
| `programaibermedia.com/pt-pt/as-nossas-convocatorias/` | As 6 modalidades aparecem como **"convocatória fechada"** e o único prazo (30/03/2026) passou. Nada a cadastrar. A convocatória é anual: entrou no **radar** ("Costuma abrir em breve") em `worker/fontes_curadas.yaml`. |
| `citeinternationaledesarts.fr/en/programmes-de-residence/` | Catálogo de programas por país. A página `/en/open-calls/` lista 4 chamadas abertas: Samsung (só nacionalidade coreana), Prisme/ADAGP (quem mora na França), Villa Arson (formados lá) e **Caméra libre** (cineastas estrangeiros em situação de perseguição; prazo 16/11/2026, confirmado na página do CNC). Só a **Caméra libre** foi cadastrada, como "verificar elegibilidade". O programa **Icatu x Cité** (anual, para artistas do Brasil) está sem chamada aberta e entrou no radar. |
| `effea.eu/effea-residencies/all-residencies` | Lista residências já concedidas. A Call #5 foi de 01/09 a 03/11/2026 e só aceita festivais e artistas com endereço em países europeus da lista, sem o Brasil. Não cadastrada. |
| `visegradfund.org/performing-artist-residency-apply` | "Currently closed" (prazo 25/10/2026) e restrita a cidadãos dos países de Visegrado (CZ, HU, PL, SK). Não cadastrada. |
| `resartis.org/open-calls/` | Agregador com ~150 chamadas de residência (título, prazo e país na listagem). Não dá para cadastrar à mão uma a uma, e o feed oficial de chamadas vem vazio. `robots.txt` permite a página; o aviso legal diz que a lista é enviada por membros, sem garantia. Seria um **coletor novo** (HTML da listagem), tratado como o On the Move: título, prazo, país e link, com elegibilidade "verificar". **Aguarda decisão.** |
| `live-composers.pantheonsite.io` | Agregador para compositores ("The Composer's Site") no domínio de plataforma `pantheonsite.io`. O `robots.txt` é `Disallow: /` para todos os robôs: **não coletamos**. Entra se vier o endereço oficial ou o link de uma chamada específica. Foi feita uma única requisição à página inicial. |

Todos aparecem na lista de fontes de `/sobre/` como inativos, com o motivo (`worker/fontes.yaml`).

### Curadoria manual · `worker/curadoria_internacional.json`

- Validada no CI pelo schema `worker/curadoria_internacional.schema.json` (JSON Schema 2020-12) e, em produção,
  pelas mesmas regras em pydantic (`worker/coletores/curadoria_internacional.py`). Entrada inválida derruba só essa
  fonte, com erro explícito.
- Cada entrada: `id`, `titulo_original`, `organizacao`, `url_oficial` (https), `tipo_oportunidade`, `pais_destino`,
  `elegibilidade_br`, `custos_cobertos`, `idioma_original`, `prazo_original`, `prazo_fuso`, `moeda`,
  `valor_original`, `linguagens`, `observacao_curta` (em português, até 280 caracteres, vira o resumo do card),
  `conferido_em` e `evidencias` (trecho literal da página oficial).
- **Regra editorial:** só entra o que foi aberto e conferido na página oficial, com prazo futuro. `aberto`,
  `requer_*` e `custos_cobertos` exigem a evidência correspondente. URLs de exemplo são recusadas. Conferência com
  mais de 45 dias gera aviso no log; prazo vencido sai sozinho.
- Hoje: 2 entradas. O **7º Fundo Ibermuseos** (inscrições de 22/10/2026 a 05/03/2027; museus dos 15 países-membros, incluindo
  o Brasil; não recebe pessoas físicas) e a **Caméra libre** (CNC/Cité internationale des arts; prazo 16/11/2026; cineastas
  estrangeiros em situação de perseguição; viagem à França paga; elegibilidade "verificar").
- Aparece como "Fonte: curadoria do portal".

## No site

- Menu: **Internacional** ao lado de Editais. Filtro "Onde fica": Brasil (padrão) · Internacional · Todos.
- Card: título original, linha em português **sem LLM** montada pelos campos ("Residência · Alemanha · cobre
  viagem e hospedagem · em inglês"), selos (elegibilidade, idioma, tipo), custos como ícones com texto alternativo,
  "Fonte: …" com link, prazo como a fonte informa (`1 dez 2026, 17h (horário de Bruxelas)`). Sem botão do assistente
  de projetos (ele é para editais brasileiros).
- "Posso me inscrever?" (só no filtro internacional): nacionalidade, país onde mora e organização.
  `requer_residencia` só aparece para quem mora no país de destino (sem resposta, aparece); `requer_parceiro`
  aparece com a explicação "precisa de uma organização parceira no país/região do edital" (some para quem diz não
  ter organização); `verificar` aparece sempre, com aviso; para quem não tem nacionalidade brasileira, "Aberto a
  brasileiros" vira "Verificar elegibilidade".
- Home: contador "oportunidades internacionais abertas" e seletor Brasil / Internacional / Todos em "Por linguagem".
  Os números do Brasil não mudam.
- `/sobre/` explica as fontes, o critério de elegibilidade e o aviso "confira sempre a chamada original; requisitos
  de visto, idioma e documentação são de responsabilidade de quem se candidata".

## Como acrescentar uma fonte nova

1. **Abra o site e confira**: `robots.txt`, termos de uso, se há RSS (`/feed/` em WordPress) e o formato do prazo.
   Se algo proibir a coleta, não ative.
2. **RSS ou API estruturada**: crie `worker/coletores/<nome>.py` com `coletar(cliente, url, intervalo_s, agora, fonte)
   -> ResultadoFonte`, devolvendo `EditalBruto` com `ambito="internacional"`. Reuse `analisar_elegibilidade` e
   `custos_cobertos`, e dê a `proveniencia` de cada campo derivado.
3. Registre o coletor em `worker/fontes_v4.py` (`_rodar_uma`), em `COLETORES_DE_EDITAIS`
   (`worker/scraper_worker.py`) e a fonte em `worker/fontes.yaml` (`tipo`, `metodo`, `url`, `termos_url`,
   `termos_conferidos_em`, `robots_ok`, intervalo). Fonte que não puder ser coletada entra com `ativa: false` e
   `motivo`.
4. **Mapas Culturais**: basta uma instância em `instancias:` com `ambito: internacional`, `pais_destino`,
   `idioma_original`.
5. **Fixtures reais** em `tests/fixtures/internacional/` e testes offline (veja `test_internacional_onthemove.py`).
6. **Sem coletor possível**: acrescente em `worker/curadoria_internacional.json`, depois de abrir a página oficial.

## Roadmap

- **Fase 2**: resumo e tradução com LLM (`resumo_pt`); câmbio pela PTAX (`valor_brl`); conversão do prazo para o
  horário de Brasília (`prazo_original` + `prazo_fuso` já estão guardados).
- **Fase 3**: Europa Criativa / EU Funding & Tenders pelo RSS do portal, com selo "requer parceiro"; TransArtists.
- **Fase 4**: editais nacionais de outros países (`ambito = estrangeiro_nacional`, hoje reservado): Chile, Portugal etc.
- Se o Ibermúsicas corrigir o certificado, ou o Iberescena publicar um feed/API aberta, ativar os coletores.
