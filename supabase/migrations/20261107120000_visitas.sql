-- Contador de visitas anônimo (LGPD): só contagens agregadas por dia × página × país × UF × cidade.
-- Nenhum IP, hash de IP ou identificador do visitante chega ao banco. Idempotente.

create table if not exists public.visitas_agregadas (
  dia     date    not null,
  pagina  text    not null check (pagina in ('home', 'editais', 'sobre')),
  pais    char(2) not null default 'ZZ',  -- 'ZZ' = desconhecido
  uf      char(2) not null default 'ZZ',  -- 'ZZ' = desconhecida ou fora do Brasil
  cidade  text    not null default '',    -- '' = desconhecida
  total   int     not null default 0 check (total >= 0),
  primary key (dia, pagina, pais, uf, cidade)
);

alter table public.visitas_agregadas enable row level security;
-- Sem policy alguma: anon e authenticated não leem nem escrevem a tabela.
revoke all on public.visitas_agregadas from anon, authenticated;
grant all on public.visitas_agregadas to service_role;

-- Incremento atômico, chamado só pela Edge Function (service_role). O dia é o de Brasília.
create or replace function public.registrar_visita_agregada(
  p_pagina text, p_pais text, p_uf text, p_cidade text
) returns void
language sql
security definer
set search_path = public
as $$
  insert into public.visitas_agregadas (dia, pagina, pais, uf, cidade, total)
  values (
    (now() at time zone 'America/Sao_Paulo')::date,
    p_pagina,
    coalesce(nullif(upper(left(p_pais, 2)), ''), 'ZZ'),
    coalesce(nullif(upper(left(p_uf, 2)), ''), 'ZZ'),
    left(coalesce(p_cidade, ''), 80),
    1
  )
  on conflict (dia, pagina, pais, uf, cidade)
  do update set total = public.visitas_agregadas.total + 1;
$$;

revoke all on function public.registrar_visita_agregada(text, text, text, text) from public, anon, authenticated;
grant execute on function public.registrar_visita_agregada(text, text, text, text) to service_role;

-- Resumo público. Cidade só aparece com 5 visitas ou mais nos últimos 30 dias;
-- o resto vira "outras cidades". `security definer` porque o anon não lê a tabela.
create or replace function public.visitas_resumo()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with hoje as (
    select (now() at time zone 'America/Sao_Paulo')::date as dia
  ),
  janela as (
    select v.* from public.visitas_agregadas v, hoje where v.dia > hoje.dia - 30
  ),
  por_uf as (
    select uf, sum(total)::int as total from janela where uf <> 'ZZ' group by uf
  ),
  cidades as (
    select cidade, uf, sum(total)::int as total
    from janela where cidade <> '' and pais = 'BR' group by cidade, uf
  )
  select jsonb_build_object(
    'total_geral', coalesce((select sum(total) from public.visitas_agregadas), 0)::int,
    'total_30d', coalesce((select sum(total) from janela), 0)::int,
    'estados_30d', (select count(*) from por_uf),
    'por_uf', coalesce(
      (select jsonb_agg(jsonb_build_object('uf', uf, 'total', total) order by total desc, uf) from por_uf),
      '[]'::jsonb),
    'top_cidades', coalesce(
      (select jsonb_agg(jsonb_build_object('cidade', cidade, 'uf', uf, 'total', total) order by total desc, cidade)
       from (select * from cidades where total >= 5 order by total desc, cidade limit 10) t),
      '[]'::jsonb),
    'outras_cidades', coalesce((select sum(total) from cidades where total < 5), 0)::int
  );
$$;

revoke all on function public.visitas_resumo() from public;
grant execute on function public.visitas_resumo() to anon, authenticated, service_role;
