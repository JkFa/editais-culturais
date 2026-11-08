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
