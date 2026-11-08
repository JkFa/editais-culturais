-- Editais internacionais (Fase 1). Idempotente.
-- Colunas novas em `editais`. O que já existe (abrangencia = nacional/estadual/municipal, dentro do
-- Brasil) não muda: o alcance geográfico das oportunidades de fora do país vai em `ambito`.
-- Todos os editais atuais ficam com ambito = 'brasil' e elegibilidade_br = 'verificar' (padrões).

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
