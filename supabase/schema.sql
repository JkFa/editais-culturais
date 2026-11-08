-- Portal de Editais Culturais: schema do Supabase (PostgreSQL).
-- Idempotente: pode ser rodado de novo sem erro (SQL Editor do Supabase).
-- Escrita só pelo worker, com a chave service_role (que ignora RLS).
-- Por isso NÃO existe policy de insert/update/delete.

create table if not exists public.editais (
  id                  bigint generated always as identity primary key,
  fonte               text not null,                       -- ex.: 'mapas:ce', 'curadoria'
  id_externo          text not null,
  chave               text generated always as (fonte || ':' || id_externo) stored,
  url_origem          text not null,
  url_inscricao       text,
  url_edital_pdf      text,
  titulo              text not null,
  orgao_fomento       text not null,
  resumo_original     text check (resumo_original is null or char_length(resumo_original) <= 2000),
  resumo_simples      text check (resumo_simples is null or char_length(resumo_simples) <= 280),
  linguagens          text[] not null default '{}'
                      check (linguagens <@ array['Música','Cinema & Audiovisual','Teatro & Dança',
                             'Fotografia','Artes Visuais','Literatura','Patrimônio','Multiáreas']::text[]),
  abrangencia         text check (abrangencia in ('nacional','estadual','municipal')),
  uf                  char(2),
  municipio           text,
  proponentes         text[]
                      check (proponentes <@ array['PF','MEI','PJ_COM_FINS','PJ_SEM_FINS','COLETIVO']::text[]),
  valor_total         numeric(14,2) check (valor_total >= 0),
  teto_por_projeto    numeric(14,2) check (teto_por_projeto >= 0),
  num_cotas           int check (num_cotas >= 0),
  exige_contrapartida boolean,                             -- nulo = não informado
  marco_legal         text,                                -- PNAB, LPG, Rouanet, Aldir Blanc...
  documentos_exigidos text[],
  cronograma          jsonb,                               -- [{"etapa": "...", "data": "..."}]
  tipo_apoio          text,                                -- ex.: 'cessao_de_espaco' (sem repasse)
  verificado_em       date,                                -- só curadoria
  pendencias          text,                                -- só curadoria
  inscricao_inicio    timestamptz,
  inscricao_fim       timestamptz not null,
  fim_tem_horario     boolean not null default false,
  publicado_em        timestamptz,
  coletado_em         timestamptz,
  atualizado_em       timestamptz,
  hash_conteudo       text,
  -- {"campo": {"via": "regex|llm|curadoria|api", "trecho": "texto literal da fonte"}}
  proveniencia        jsonb not null default '{}'::jsonb,
  busca_textual       tsvector generated always as (
                        setweight(to_tsvector('portuguese', coalesce(titulo, '')), 'A') ||
                        setweight(to_tsvector('portuguese', coalesce(orgao_fomento, '')), 'B') ||
                        setweight(to_tsvector('portuguese', coalesce(resumo_simples, '')), 'C')
                      ) stored,
  constraint editais_fonte_id_externo_key unique (fonte, id_externo),
  constraint editais_nacional_sem_uf check (abrangencia is distinct from 'nacional' or uf is null)
);

create index if not exists editais_busca_textual_idx on public.editais using gin (busca_textual);
create index if not exists editais_linguagens_idx    on public.editais using gin (linguagens);
create index if not exists editais_inscricao_fim_idx on public.editais (inscricao_fim);
create index if not exists editais_uf_idx            on public.editais (uf);

-- Editais recorrentes hoje fechados ("Costuma abrir em breve"). Nunca contam como abertos.
create table if not exists public.radar_editais (
  id                        bigint generated always as identity primary key,
  id_externo                text not null unique,
  titulo                    text not null,
  orgao_fomento             text not null,
  url_referencia            text,
  ultima_edicao             text,
  janela_habitual           text,
  janela_mes_inicio         smallint check (janela_mes_inicio between 1 and 12),
  janela_mes_fim            smallint check (janela_mes_fim between 1 and 12),
  proponentes_ultima_edicao text[]
                            check (proponentes_ultima_edicao <@ array['PF','MEI','PJ_COM_FINS','PJ_SEM_FINS','COLETIVO']::text[]),
  linguagens                text[] not null default '{}'
                            check (linguagens <@ array['Música','Cinema & Audiovisual','Teatro & Dança',
                                   'Fotografia','Artes Visuais','Literatura','Patrimônio','Multiáreas']::text[]),
  verificado_em             date,
  atualizado_em             timestamptz not null default now()
);

-- Batimento do ETL: prova que o worker rodou (e mantém o projeto ativo no plano grátis).
create table if not exists public.heartbeat (
  id          int primary key,
  ultimo_ping timestamptz
);

-- View consumida pelo site. security_invoker faz a RLS de `editais` valer para quem consulta.
create or replace view public.editais_abertos
with (security_invoker = true) as
  select * from public.editais where inscricao_fim >= now();

-- Apaga editais encerrados há mais de 180 dias, para caber nos 500 MB do plano grátis.
create or replace function public.limpar_editais_antigos()
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  apagados integer;
begin
  delete from public.editais where inscricao_fim < now() - interval '180 days';
  get diagnostics apagados = row_count;
  return apagados;
end;
$$;

-- RLS: ligada nas três tabelas. O site (anon) só lê editais, radar e a view.
alter table public.editais       enable row level security;
alter table public.radar_editais enable row level security;
alter table public.heartbeat     enable row level security;

drop policy if exists "leitura publica de editais" on public.editais;
create policy "leitura publica de editais"
  on public.editais for select to anon, authenticated using (true);

drop policy if exists "leitura publica do radar" on public.radar_editais;
create policy "leitura publica do radar"
  on public.radar_editais for select to anon, authenticated using (true);

-- Defesa em profundidade: mesmo com RLS, o anon nem recebe o privilégio de escrita.
revoke all on public.editais, public.radar_editais, public.heartbeat from anon, authenticated;
revoke all on public.editais_abertos from anon, authenticated;
grant select on public.editais, public.radar_editais, public.editais_abertos to anon, authenticated;
grant all on public.editais, public.radar_editais, public.heartbeat to service_role;
grant select on public.editais_abertos to service_role;

revoke all on function public.limpar_editais_antigos() from public, anon, authenticated;
grant execute on function public.limpar_editais_antigos() to service_role;

-- ---------------------------------------------------------------- v4: fontes novas

alter table public.editais add column if not exists via text;                        -- "via CultBR (MinC)"
alter table public.editais add column if not exists fontes_alternativas jsonb;        -- [{fonte,url,via}]
alter table public.editais add column if not exists requisitos text[];                -- ex.: "Exige projeto aprovado na Lei Rouanet"

alter table public.editais drop constraint if exists editais_tipo_apoio_check;
alter table public.editais add constraint editais_tipo_apoio_check check (
  tipo_apoio is null or tipo_apoio in
  ('fomento_em_dinheiro', 'premio', 'patrocinio_incentivado', 'cessao_de_espaco', 'credenciamento')
);

-- A view `editais_abertos` usa `select *`: recriada para incluir as colunas novas (ao final da lista).
create or replace view public.editais_abertos
with (security_invoker = true) as
  select * from public.editais where inscricao_fim >= now();

create table if not exists public.fontes_status (
  fonte               text primary key,           -- id da fonte (ex.: 'mapas:ce', 'minc-inscricoes-abertas')
  nome                text not null,
  tipo                text not null check (tipo in ('oficial', 'plataforma', 'descoberta')),
  metodo              text not null,
  url                 text not null,
  ativa               boolean not null,
  status              text not null check (status in
                        ('ok', 'erro', 'bloqueada_em_ci', 'desativada', 'sem_dados',
                         'conteudo_insuficiente', 'bloqueada_por_robots')),
  erro                text,
  motivo              text,                       -- por que está desativada
  ultima_execucao     timestamptz,
  ultimo_sucesso      timestamptz,
  total_coletado      int not null default 0,
  candidatos_criados  int not null default 0,
  duracao_ms          int not null default 0,
  termos_url          text,
  termos_conferidos_em text
);

alter table public.fontes_status enable row level security;
drop policy if exists "leitura publica de fontes_status" on public.fontes_status;
create policy "leitura publica de fontes_status"
  on public.fontes_status for select to anon, authenticated using (true);
revoke all on public.fontes_status from anon, authenticated;
grant select on public.fontes_status to anon, authenticated;
grant all on public.fontes_status to service_role;

-- ── Assistente de Projetos Culturais (migration 20261108000000_assistente.sql) ──
-- Assistente de Projetos Culturais (Fase 1). Idempotente.
-- RLS em tudo: cada usuário só enxerga os próprios projetos. Quem escreve mensagens, respostas e o
-- contador de uso é a Edge Function `assistente` (service_role); o navegador só lê essas tabelas.
-- LGPD: os dados pessoais do proponente ficam em `projeto_proponente`, que a função nunca consulta.

create or replace function public.assistente_toca_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ── projetos ────────────────────────────────────────────────────────────────────────────────
create table if not exists public.projetos (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null default auth.uid() references auth.users(id) on delete cascade,
  -- O edital some do banco quando vence; por isso o projeto guarda uma cópia de título e links.
  edital_id     bigint references public.editais(id) on delete set null,
  edital_chave  text,
  edital_titulo text check (edital_titulo is null or char_length(edital_titulo) <= 300),
  edital_url    text,
  fluxo         text not null default 'padrao' check (fluxo in ('padrao', 'audiovisual')),
  titulo        text not null check (char_length(titulo) between 1 and 200),
  ficha         jsonb not null default '{}'::jsonb,
  status        text not null default 'rascunho',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists projetos_user_idx on public.projetos (user_id, updated_at desc);
drop trigger if exists projetos_updated_at on public.projetos;
create trigger projetos_updated_at before update on public.projetos
  for each row execute function public.assistente_toca_updated_at();

-- Dados pessoais do proponente: coletados em formulário próprio e NUNCA enviados ao LLM.
create table if not exists public.projeto_proponente (
  projeto_id uuid primary key references public.projetos(id) on delete cascade,
  dados      jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
drop trigger if exists proponente_updated_at on public.projeto_proponente;
create trigger proponente_updated_at before update on public.projeto_proponente
  for each row execute function public.assistente_toca_updated_at();

-- ── seções, histórico, respostas e mensagens ───────────────────────────────────────────────
create table if not exists public.projeto_secoes (
  id          uuid primary key default gen_random_uuid(),
  projeto_id  uuid not null references public.projetos(id) on delete cascade,
  secao_id    text not null check (secao_id ~ '^[a-z_]{2,40}$'),
  rascunho    text not null default '',
  versao      int not null default 1,
  aprovado    boolean not null default false,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (projeto_id, secao_id)
);
drop trigger if exists projeto_secoes_updated_at on public.projeto_secoes;
create trigger projeto_secoes_updated_at before update on public.projeto_secoes
  for each row execute function public.assistente_toca_updated_at();

create table if not exists public.projeto_secoes_hist (
  id                uuid primary key default gen_random_uuid(),
  projeto_secao_id  uuid not null references public.projeto_secoes(id) on delete cascade,
  rascunho          text not null,
  versao            int not null,
  created_at        timestamptz not null default now()
);
create index if not exists projeto_secoes_hist_idx on public.projeto_secoes_hist (projeto_secao_id, versao desc);

-- Cada mudança do texto guarda a versão anterior e sobe a versão. Editar tira a aprovação.
create or replace function public.projeto_secoes_versiona() returns trigger
language plpgsql
security definer  -- o usuário só lê o histórico; quem grava é o trigger
set search_path = public
as $$
begin
  if new.rascunho is distinct from old.rascunho then
    if old.rascunho <> '' then
      insert into public.projeto_secoes_hist (projeto_secao_id, rascunho, versao)
      values (old.id, old.rascunho, old.versao);
    end if;
    new.versao := old.versao + 1;
    if new.aprovado = old.aprovado then
      new.aprovado := false;
    end if;
  end if;
  return new;
end;
$$;
drop trigger if exists projeto_secoes_versiona on public.projeto_secoes;
create trigger projeto_secoes_versiona before update on public.projeto_secoes
  for each row execute function public.projeto_secoes_versiona();

create table if not exists public.respostas (
  id          uuid primary key default gen_random_uuid(),
  projeto_id  uuid not null references public.projetos(id) on delete cascade,
  secao_id    text not null,
  pergunta_id text not null,
  resposta    text not null check (char_length(resposta) <= 8000),
  created_at  timestamptz not null default now()
);
create index if not exists respostas_idx on public.respostas (projeto_id, secao_id, created_at);

create table if not exists public.mensagens (
  id          uuid primary key default gen_random_uuid(),
  projeto_id  uuid not null references public.projetos(id) on delete cascade,
  secao_id    text not null,
  papel       text not null check (papel in ('usuario', 'assistente')),
  conteudo    jsonb not null,
  created_at  timestamptz not null default now()
);
create index if not exists mensagens_idx on public.mensagens (projeto_id, secao_id, created_at);

-- ── limite diário ───────────────────────────────────────────────────────────────────────────
create table if not exists public.uso_diario (
  user_id   uuid not null references auth.users(id) on delete cascade,
  data      date not null,
  chamadas  int not null default 0 check (chamadas >= 0),
  primary key (user_id, data)
);

-- Incremento atômico. Devolve o total do dia, ou -1 se o limite já foi atingido (sem incrementar).
create or replace function public.assistente_consumir_uso(p_user uuid, p_limite int)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  v int;
begin
  insert into public.uso_diario (user_id, data, chamadas)
  values (p_user, (now() at time zone 'America/Sao_Paulo')::date, 1)
  on conflict (user_id, data)
  do update set chamadas = public.uso_diario.chamadas + 1
     where public.uso_diario.chamadas < p_limite
  returning chamadas into v;
  return coalesce(v, -1);
end;
$$;
revoke all on function public.assistente_consumir_uso(uuid, int) from public, anon, authenticated;
grant execute on function public.assistente_consumir_uso(uuid, int) to service_role;

-- ── critérios do edital (só leitura; preenchida em fases futuras) ───────────────────────────
create table if not exists public.edital_criterios (
  edital_id          bigint primary key references public.editais(id) on delete cascade,
  criterios          jsonb not null default '[]'::jsonb,
  teto_orcamento     numeric(14,2),
  linguagens         text[] not null default '{}',
  campos_exigidos    jsonb not null default '{}'::jsonb,
  limites_caracteres jsonb not null default '{}'::jsonb,
  extraido_em        timestamptz,
  revisado           boolean not null default false
);

-- ── RLS ─────────────────────────────────────────────────────────────────────────────────────
alter table public.projetos            enable row level security;
alter table public.projeto_proponente  enable row level security;
alter table public.projeto_secoes      enable row level security;
alter table public.projeto_secoes_hist enable row level security;
alter table public.respostas           enable row level security;
alter table public.mensagens           enable row level security;
alter table public.uso_diario          enable row level security;
alter table public.edital_criterios    enable row level security;

drop policy if exists "projetos: dono" on public.projetos;
create policy "projetos: dono" on public.projetos
  for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists "proponente: dono do projeto" on public.projeto_proponente;
create policy "proponente: dono do projeto" on public.projeto_proponente
  for all to authenticated
  using (exists (select 1 from public.projetos p where p.id = projeto_id and p.user_id = auth.uid()))
  with check (exists (select 1 from public.projetos p where p.id = projeto_id and p.user_id = auth.uid()));

drop policy if exists "secoes: dono do projeto" on public.projeto_secoes;
create policy "secoes: dono do projeto" on public.projeto_secoes
  for all to authenticated
  using (exists (select 1 from public.projetos p where p.id = projeto_id and p.user_id = auth.uid()))
  with check (exists (select 1 from public.projetos p where p.id = projeto_id and p.user_id = auth.uid()));

drop policy if exists "historico: dono do projeto" on public.projeto_secoes_hist;
create policy "historico: dono do projeto" on public.projeto_secoes_hist
  for select to authenticated
  using (exists (
    select 1 from public.projeto_secoes s join public.projetos p on p.id = s.projeto_id
    where s.id = projeto_secao_id and p.user_id = auth.uid()));

drop policy if exists "respostas: dono do projeto" on public.respostas;
create policy "respostas: dono do projeto" on public.respostas
  for select to authenticated
  using (exists (select 1 from public.projetos p where p.id = projeto_id and p.user_id = auth.uid()));

drop policy if exists "mensagens: dono do projeto" on public.mensagens;
create policy "mensagens: dono do projeto" on public.mensagens
  for select to authenticated
  using (exists (select 1 from public.projetos p where p.id = projeto_id and p.user_id = auth.uid()));

drop policy if exists "uso: o proprio usuario" on public.uso_diario;
create policy "uso: o proprio usuario" on public.uso_diario
  for select to authenticated using (user_id = auth.uid());

drop policy if exists "criterios: logados leem" on public.edital_criterios;
create policy "criterios: logados leem" on public.edital_criterios
  for select to authenticated using (true);

-- ── permissões ──────────────────────────────────────────────────────────────────────────────
revoke all on public.projetos, public.projeto_proponente, public.projeto_secoes,
  public.projeto_secoes_hist, public.respostas, public.mensagens, public.uso_diario,
  public.edital_criterios from anon, authenticated;
grant select, insert, update, delete on public.projetos, public.projeto_proponente,
  public.projeto_secoes to authenticated;
grant select on public.projeto_secoes_hist, public.respostas, public.mensagens,
  public.uso_diario, public.edital_criterios to authenticated;
grant all on public.projetos, public.projeto_proponente, public.projeto_secoes,
  public.projeto_secoes_hist, public.respostas, public.mensagens, public.uso_diario,
  public.edital_criterios to service_role;

-- ---------------------------------------------------------------- internacional (Fase 1)


alter table public.editais add column if not exists ambito text not null default 'brasil';
alter table public.editais add column if not exists pais_destino text;                 -- ISO-3166 alfa-2, 'Ibero-América', 'Europa', 'Online'
alter table public.editais add column if not exists elegibilidade_br text not null default 'verificar';
alter table public.editais add column if not exists tipo_oportunidade text;
alter table public.editais add column if not exists custos_cobertos text[];            -- só o que a fonte escreve
alter table public.editais add column if not exists idioma_original text;
alter table public.editais add column if not exists moeda text;
alter table public.editais add column if not exists valor_original numeric(14,2);
alter table public.editais add column if not exists valor_brl numeric(14,2);           -- vazio na Fase 1 (câmbio na Fase 2)
alter table public.editais add column if not exists prazo_original timestamptz;        -- como a fonte informa
alter table public.editais add column if not exists prazo_fuso text;                   -- ex.: 'Europe/Brussels'
alter table public.editais add column if not exists titulo_original text;
alter table public.editais add column if not exists resumo_pt text;                    -- vazio na Fase 1 (tradução na Fase 2)

alter table public.editais drop constraint if exists editais_ambito_check;
alter table public.editais add constraint editais_ambito_check
  check (ambito in ('brasil', 'internacional', 'estrangeiro_nacional'));

alter table public.editais drop constraint if exists editais_elegibilidade_br_check;
alter table public.editais add constraint editais_elegibilidade_br_check
  check (elegibilidade_br in ('aberto', 'requer_residencia', 'requer_parceiro', 'verificar'));

alter table public.editais drop constraint if exists editais_tipo_oportunidade_check;
alter table public.editais add constraint editais_tipo_oportunidade_check
  check (tipo_oportunidade is null or tipo_oportunidade in
    ('residencia', 'mobilidade', 'projeto', 'premio', 'formacao', 'apresentacao', 'outro'));

alter table public.editais drop constraint if exists editais_custos_cobertos_check;
alter table public.editais add constraint editais_custos_cobertos_check
  check (custos_cobertos is null or custos_cobertos <@
    array['viagem', 'hospedagem', 'cache', 'producao', 'alimentacao', 'acessibilidade']::text[]);

alter table public.editais drop constraint if exists editais_valor_original_check;
alter table public.editais add constraint editais_valor_original_check
  check (valor_original is null or valor_original >= 0);

create index if not exists editais_ambito_idx on public.editais (ambito);

-- A view usa `select *`, que é expandido na criação: recriada para incluir as colunas novas.
create or replace view public.editais_abertos
with (security_invoker = true) as
  select * from public.editais where inscricao_fim >= now();

-- ── Assistente, Fase 2 (migration 20261109120000_assistente_fase2.sql) ──

-- ── administradores ─────────────────────────────────────────────────────────────────────────
create table if not exists public.admins (
  user_id    uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);
alter table public.admins enable row level security;

-- `security definer`: a policy de outras tabelas pergunta "sou admin?" sem precisar ler `admins`.
create or replace function public.eh_admin() returns boolean
language sql stable security definer set search_path = public
as $$ select exists (select 1 from public.admins where user_id = auth.uid()) $$;
revoke all on function public.eh_admin() from public, anon;
grant execute on function public.eh_admin() to authenticated, service_role;

drop policy if exists "admins: cada um vê a própria linha" on public.admins;
create policy "admins: cada um vê a própria linha" on public.admins
  for select to authenticated using (user_id = auth.uid());

-- ── orçamento ───────────────────────────────────────────────────────────────────────────────
create table if not exists public.orcamento_itens (
  id             uuid primary key default gen_random_uuid(),
  projeto_id     uuid not null references public.projetos(id) on delete cascade,
  etapa          text not null check (etapa in
                   ('pre_producao', 'producao', 'pos_producao', 'divulgacao', 'acessibilidade', 'administrativo')),
  rubrica        text not null default '' check (char_length(rubrica) <= 120),
  item           text not null check (char_length(item) between 1 and 200),
  unidade        text not null default 'unidade' check (char_length(unidade) <= 40),
  quantidade     numeric(14,3) not null default 1 check (quantidade >= 0),
  valor_unitario numeric(14,2) check (valor_unitario is null or valor_unitario >= 0),   -- nulo = "valor a confirmar"
  fonte_preco    text check (fonte_preco is null or char_length(fonte_preco) <= 300),
  justificativa  text check (justificativa is null or char_length(justificativa) <= 600),
  ordem          int not null default 0,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index if not exists orcamento_itens_projeto_idx on public.orcamento_itens (projeto_id, ordem);

-- ── cronograma ──────────────────────────────────────────────────────────────────────────────
create table if not exists public.cronograma_itens (
  id         uuid primary key default gen_random_uuid(),
  projeto_id uuid not null references public.projetos(id) on delete cascade,
  etapa      text not null check (etapa in
               ('pre_producao', 'producao', 'pos_producao', 'divulgacao', 'acessibilidade', 'administrativo')),
  atividade  text not null check (char_length(atividade) between 1 and 200),
  mes_inicio int not null check (mes_inicio between 1 and 120),
  mes_fim    int not null check (mes_fim between 1 and 120),
  ordem      int not null default 0,
  created_at timestamptz not null default now(),
  check (mes_fim >= mes_inicio)
);
create index if not exists cronograma_itens_projeto_idx on public.cronograma_itens (projeto_id, ordem);

-- ── plano de mídia ──────────────────────────────────────────────────────────────────────────
create table if not exists public.midia_itens (
  id                uuid primary key default gen_random_uuid(),
  projeto_id        uuid not null references public.projetos(id) on delete cascade,
  peca              text not null check (char_length(peca) between 1 and 200),
  canal             text not null default '' check (char_length(canal) <= 120),
  periodo           text not null default '' check (char_length(periodo) <= 120),
  quantidade        numeric(14,3) not null default 1 check (quantidade >= 0),
  custo             numeric(14,2) check (custo is null or custo >= 0),
  orcamento_item_id uuid references public.orcamento_itens(id) on delete set null,
  ordem             int not null default 0,
  created_at        timestamptz not null default now()
);
create index if not exists midia_itens_projeto_idx on public.midia_itens (projeto_id, ordem);

-- ── equipe (sem CPF, RG ou qualquer documento) ──────────────────────────────────────────────
create table if not exists public.equipe_membros (
  id                       uuid primary key default gen_random_uuid(),
  projeto_id               uuid not null references public.projetos(id) on delete cascade,
  funcao                   text not null check (char_length(funcao) between 1 and 120),
  nome_artistico_ou_funcao text not null default '' check (char_length(nome_artistico_ou_funcao) <= 160),
  mini_curriculo           text not null default '' check (char_length(mini_curriculo) <= 900),
  local                    text not null default '' check (char_length(local) <= 120),
  orcamento_item_ids       uuid[] not null default '{}',
  ordem                    int not null default 0,
  created_at               timestamptz not null default now()
);
create index if not exists equipe_membros_projeto_idx on public.equipe_membros (projeto_id, ordem);

-- ── critérios do edital: colunas novas (a tabela já existe da Fase 1) ───────────────────────
alter table public.edital_criterios add column if not exists fonte_url text;
alter table public.edital_criterios add column if not exists fonte_hash text;
alter table public.edital_criterios add column if not exists teto_por_proponente numeric(14,2);
alter table public.edital_criterios add column if not exists percentuais_max jsonb not null default '{}'::jsonb;
alter table public.edital_criterios add column if not exists percentuais_min jsonb not null default '{}'::jsonb;  -- ex.: {"acessibilidade": 0.02}
alter table public.edital_criterios add column if not exists rubricas_vedadas text[] not null default '{}';
alter table public.edital_criterios add column if not exists contrapartida_exigida text;
alter table public.edital_criterios add column if not exists prazo_execucao_meses int;
alter table public.edital_criterios add column if not exists exige_acessibilidade boolean;
alter table public.edital_criterios add column if not exists observacoes text;
-- de onde veio cada valor: {campo: {"trecho": "...", "pagina": 12}} (a tela de revisão mostra ao lado do documento)
alter table public.edital_criterios add column if not exists evidencias jsonb not null default '{}'::jsonb;
alter table public.edital_criterios add column if not exists modelo_llm text;
alter table public.edital_criterios add column if not exists revisado_em timestamptz;
alter table public.edital_criterios add column if not exists revisado_por uuid references auth.users(id) on delete set null;
-- `campos_exigidos` é uma LISTA ordenada [{nome, secao}] (jsonb de objeto não guarda a ordem das chaves).
alter table public.edital_criterios alter column campos_exigidos set default '[]'::jsonb;

alter table public.edital_criterios drop constraint if exists edital_criterios_valores_check;
alter table public.edital_criterios add constraint edital_criterios_valores_check check (
  (teto_orcamento is null or teto_orcamento > 0)
  and (teto_por_proponente is null or teto_por_proponente > 0)
  and (prazo_execucao_meses is null or prazo_execucao_meses between 1 and 120)
  and jsonb_typeof(criterios) = 'array'
  and jsonb_typeof(percentuais_max) = 'object'
  and jsonb_typeof(percentuais_min) = 'object'
  and jsonb_typeof(evidencias) = 'object'
  and jsonb_typeof(limites_caracteres) = 'object'
);

-- ── fila de extração ────────────────────────────────────────────────────────────────────────
create table if not exists public.extracao_fila (
  edital_id     bigint primary key references public.editais(id) on delete cascade,
  status        text not null default 'pendente'
                  check (status in ('pendente', 'processando', 'ok', 'erro', 'sem_documento')),
  tentativas    int not null default 0 check (tentativas >= 0),
  ultimo_erro   text,
  atualizado_em timestamptz not null default now()
);
create index if not exists extracao_fila_status_idx on public.extracao_fila (status, atualizado_em);

-- Lista de revisão: critérios ainda não conferidos, os mais usados primeiro (projetos criados para aquele edital).
-- `security definer` porque o administrador não lê os projetos dos outros; devolve linhas SÓ para administrador.
create or replace function public.admin_criterios_para_revisar()
returns table (
  edital_id bigint, titulo text, url_origem text, url_edital_pdf text, inscricao_fim timestamptz,
  projetos int, extraido_em timestamptz, fila_status text, fila_erro text
)
language sql stable security definer set search_path = public
as $$
  select c.edital_id, e.titulo, e.url_origem, e.url_edital_pdf, e.inscricao_fim,
         (select count(*)::int from public.projetos p where p.edital_id = c.edital_id),
         c.extraido_em, f.status, f.ultimo_erro
  from public.edital_criterios c
  join public.editais e on e.id = c.edital_id
  left join public.extracao_fila f on f.edital_id = c.edital_id
  where public.eh_admin() and c.revisado = false
  order by 6 desc, e.inscricao_fim asc
$$;
revoke all on function public.admin_criterios_para_revisar() from public, anon;
grant execute on function public.admin_criterios_para_revisar() to authenticated, service_role;

-- ── RLS ─────────────────────────────────────────────────────────────────────────────────────
alter table public.orcamento_itens  enable row level security;
alter table public.cronograma_itens enable row level security;
alter table public.midia_itens      enable row level security;
alter table public.equipe_membros   enable row level security;
alter table public.extracao_fila    enable row level security;

do $$
declare t text;
begin
  foreach t in array array['orcamento_itens', 'cronograma_itens', 'midia_itens', 'equipe_membros'] loop
    execute format('drop policy if exists %I on public.%I', t || ': dono do projeto', t);
    execute format(
      'create policy %I on public.%I for select to authenticated using (
         exists (select 1 from public.projetos p where p.id = projeto_id and p.user_id = auth.uid()))',
      t || ': dono do projeto', t);
  end loop;
end $$;

-- Critérios: todo usuário logado lê (já existia); só administrador corrige e marca como revisado.
drop policy if exists "criterios: admin corrige" on public.edital_criterios;
create policy "criterios: admin corrige" on public.edital_criterios
  for update to authenticated using (public.eh_admin()) with check (public.eh_admin());

drop policy if exists "fila: admin le" on public.extracao_fila;
create policy "fila: admin le" on public.extracao_fila for select to authenticated using (public.eh_admin());
drop policy if exists "fila: admin reprocessa" on public.extracao_fila;
create policy "fila: admin reprocessa" on public.extracao_fila
  for update to authenticated using (public.eh_admin()) with check (public.eh_admin());

-- ── permissões ──────────────────────────────────────────────────────────────────────────────
revoke all on public.orcamento_itens, public.cronograma_itens, public.midia_itens, public.equipe_membros,
  public.extracao_fila, public.admins from anon, authenticated;
grant select on public.orcamento_itens, public.cronograma_itens, public.midia_itens,
  public.equipe_membros to authenticated;
grant select on public.admins to authenticated;
grant select, update on public.extracao_fila to authenticated;
-- o administrador só altera o que a revisão mexe; o resto do registro é do worker
revoke update on public.edital_criterios from authenticated;
grant update (criterios, teto_orcamento, teto_por_proponente, percentuais_max, percentuais_min, rubricas_vedadas,
  limites_caracteres, campos_exigidos, contrapartida_exigida, prazo_execucao_meses, exige_acessibilidade,
  observacoes, revisado, revisado_em, revisado_por) on public.edital_criterios to authenticated;
revoke update on public.extracao_fila from authenticated;
grant update (status, tentativas, ultimo_erro, atualizado_em) on public.extracao_fila to authenticated;
grant all on public.orcamento_itens, public.cronograma_itens, public.midia_itens, public.equipe_membros,
  public.extracao_fila, public.admins, public.edital_criterios to service_role;
-- Placar de cobertura (worker/cobertura.py): uma linha por dia com a nota e as métricas.
-- Leitura pública (o /sobre/ mostra o placar); escrita só pela service_role (o worker).
create table if not exists public.cobertura_diaria (
  data date primary key,
  nota numeric(4,2) not null check (nota >= 0 and nota <= 10),
  metricas jsonb not null default '[]'::jsonb check (jsonb_typeof(metricas) = 'array'),
  por_uf jsonb not null default '[]'::jsonb check (jsonb_typeof(por_uf) = 'array'),
  criado_em timestamptz not null default now()
);

alter table public.cobertura_diaria enable row level security;

drop policy if exists cobertura_leitura_publica on public.cobertura_diaria;
create policy cobertura_leitura_publica on public.cobertura_diaria for select to anon, authenticated using (true);

revoke all on public.cobertura_diaria from public, anon, authenticated;
grant select on public.cobertura_diaria to anon, authenticated;
grant all on public.cobertura_diaria to service_role;

-- ---------------------------------------------------------------- verificação (selo automático)
-- Selo "verificado automaticamente": quem conferiu o edital, uma pessoa (curadoria) ou um programa (automatica).
-- Nulo = fonte estruturada (Mapas, CultBR...) ou sem selo. O site só mostra selo para 'automatica'.
alter table public.editais add column if not exists verificacao text;

alter table public.editais drop constraint if exists editais_verificacao_check;
alter table public.editais add constraint editais_verificacao_check
  check (verificacao is null or verificacao in ('curadoria', 'automatica'));

update public.editais set verificacao = 'curadoria' where fonte = 'curadoria' and verificacao is null;

-- A view usa `select *`, que é expandido na criação: recriada para incluir a coluna nova.
create or replace view public.editais_abertos
with (security_invoker = true) as
  select * from public.editais where inscricao_fim >= now();
