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
