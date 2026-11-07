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
