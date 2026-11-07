-- v4: fontes novas. Colunas extras em editais, tabela de saúde das fontes e checagem de tipo_apoio.
-- Idempotente.

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
