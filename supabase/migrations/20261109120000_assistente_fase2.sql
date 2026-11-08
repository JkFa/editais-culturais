-- Assistente de Projetos Culturais, Fase 2. Idempotente.
-- Orçamento, cronograma, plano de mídia e equipe viram tabelas (RLS por dono do projeto). O usuário só
-- LÊ essas tabelas; quem grava é a Edge Function `assistente` (service_role), que valida, recalcula
-- os totais e confere de quem é o projeto antes de salvar. `equipe_membros` não tem CPF nem RG.
-- Também prepara a extração automática de critérios dos editais (fila + revisão por administrador).

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
