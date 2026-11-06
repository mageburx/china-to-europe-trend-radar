-- Execute no Supabase SQL Editor, após criar projeto e utilizador(s) Auth.
-- NÃO inserir chaves de serviço no GitHub, Streamlit ou browser.
-- A camada de dados privados fica fora do GitHub público.
create extension if not exists pgcrypto;

create table if not exists public.radar_supplier_quotes (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  product_id text not null check (length(product_id) between 1 and 120),
  supplier_name text not null check (length(supplier_name) between 1 and 200),
  supplier_url text not null check (supplier_url ~ '^https://[^[:space:]]+$'),
  currency text not null default 'EUR' check (currency='EUR'),
  unit_cost_eur numeric(12,2) not null check (unit_cost_eur >= 0),
  order_quantity integer not null check (order_quantity > 0),
  freight_total_eur numeric(12,2) not null default 0 check (freight_total_eur >= 0),
  import_cost_total_eur numeric(12,2) not null default 0 check (import_cost_total_eur >= 0),
  checked_on date not null default current_date,
  compliance_status text not null default 'unchecked' check (compliance_status in ('unchecked','review','cleared','blocked')),
  notes text not null default '',
  created_at timestamptz not null default now()
);

create table if not exists public.radar_competitor_prices (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  product_id text not null check (length(product_id) between 1 and 120),
  market text not null check (market in ('PT','ES')),
  merchant text not null check (length(merchant) between 1 and 200),
  evidence_url text not null check (evidence_url ~ '^https://[^[:space:]]+$'),
  retail_price_gross_eur numeric(12,2) not null check (retail_price_gross_eur >= 0),
  checked_on date not null default current_date,
  notes text not null default '',
  created_at timestamptz not null default now()
);

create index if not exists idx_radar_quote_owner_product on public.radar_supplier_quotes (owner_id,product_id,checked_on desc);
create index if not exists idx_radar_price_owner_product on public.radar_competitor_prices (owner_id,product_id,checked_on desc);

alter table public.radar_supplier_quotes enable row level security;
alter table public.radar_competitor_prices enable row level security;

-- Explicitly revoke potentially broad default permissions. RLS and grants BOTH matter.
revoke all on public.radar_supplier_quotes from public, anon, authenticated;
revoke all on public.radar_competitor_prices from public, anon, authenticated;
grant select,insert,update,delete on public.radar_supplier_quotes to authenticated;
grant select,insert,update,delete on public.radar_competitor_prices to authenticated;

-- No anon policies. Every authenticated user can see/edit ONLY their own rows.
drop policy if exists radar_quote_owner on public.radar_supplier_quotes;
create policy radar_quote_owner on public.radar_supplier_quotes
  for all to authenticated using ((select auth.uid())=owner_id)
  with check ((select auth.uid())=owner_id);
drop policy if exists radar_price_owner on public.radar_competitor_prices;
create policy radar_price_owner on public.radar_competitor_prices
  for all to authenticated using ((select auth.uid())=owner_id)
  with check ((select auth.uid())=owner_id);
