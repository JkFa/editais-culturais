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
