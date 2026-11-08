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
