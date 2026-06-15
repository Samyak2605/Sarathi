-- Run once in the Supabase SQL editor before switching SARATHI_MODE=live.
-- Mirrors gateway/storage/sqlite_store.py's schema, plus a pgvector index
-- and RPC function for semantic cache search (embedding dim = 384, matches
-- BAAI/bge-small-en-v1.5 via fastembed).

create extension if not exists vector;

create table if not exists api_keys (
    key text primary key,
    name text not null,
    daily_token_budget integer not null,
    daily_cost_budget_inr real not null,
    rate_limit_per_minute integer not null,
    created_at double precision not null,
    active boolean not null default true
);

create table if not exists usage_records (
    id text primary key,
    api_key text not null,
    created_at double precision not null,
    model_requested text not null,
    model_used text not null,
    route_tier text,
    route_reason text,
    prompt_tokens integer not null,
    completion_tokens integer not null,
    total_tokens integer not null,
    cost_inr real not null,
    latency_ms real not null,
    cache_status text not null,
    outcome text not null,
    provider text not null,
    failover_chain jsonb not null default '[]',
    error_type text,
    stream boolean not null default false
);
create index if not exists idx_usage_key_time on usage_records(api_key, created_at);

create table if not exists cache_entries (
    id text primary key,
    namespace text not null,
    prompt_hash text not null,
    prompt_text text not null,
    embedding vector(384),
    response_json text not null,
    model_used text not null,
    created_at double precision not null,
    expires_at double precision not null
);
create index if not exists idx_cache_ns_hash on cache_entries(namespace, prompt_hash);
create index if not exists idx_cache_embedding on cache_entries
    using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create or replace function match_cache_entries(
    p_namespace text,
    p_query_embedding vector(384),
    p_match_count int default 1
)
returns table (
    id text,
    namespace text,
    prompt_hash text,
    prompt_text text,
    embedding vector(384),
    response_json text,
    model_used text,
    created_at double precision,
    expires_at double precision,
    similarity double precision
)
language sql stable
as $$
    select
        id, namespace, prompt_hash, prompt_text, embedding, response_json,
        model_used, created_at, expires_at,
        1 - (embedding <=> p_query_embedding) as similarity
    from cache_entries
    where namespace = p_namespace
      and expires_at > extract(epoch from now())
      and embedding is not null
    order by embedding <=> p_query_embedding
    limit p_match_count;
$$;
