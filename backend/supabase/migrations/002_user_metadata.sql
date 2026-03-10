-- ============================================================
-- Migration 002: user_metadata table
-- ============================================================

CREATE TABLE IF NOT EXISTS public.user_metadata (
    id              UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    username        TEXT        UNIQUE,
    bio             TEXT,
    trade_interests TEXT[]      NOT NULL DEFAULT '{}',
    setup_complete  BOOLEAN     NOT NULL DEFAULT FALSE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.user_metadata              IS 'Extended user preferences and onboarding state; one row per auth.users record.';
COMMENT ON COLUMN public.user_metadata.trade_interests IS 'Array of trade categories the user is interested in (plumbing, electrical, etc.).';
COMMENT ON COLUMN public.user_metadata.setup_complete  IS 'True once the user has completed the onboarding signup flow.';

-- ── Row Level Security ────────────────────────────────────────

ALTER TABLE public.user_metadata ENABLE ROW LEVEL SECURITY;

CREATE POLICY "user_metadata: select own"
    ON public.user_metadata FOR SELECT
    USING (auth.uid() = id);

CREATE POLICY "user_metadata: insert own"
    ON public.user_metadata FOR INSERT
    WITH CHECK (auth.uid() = id);

CREATE POLICY "user_metadata: update own"
    ON public.user_metadata FOR UPDATE
    USING (auth.uid() = id);

-- ── Auto-update updated_at on row changes ────────────────────

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER user_metadata_updated_at
    BEFORE UPDATE ON public.user_metadata
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ── Update handle_new_user to also create user_metadata row ──
-- (Re-creates the trigger function defined in migration 001)

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.profiles (id)
    VALUES (NEW.id)
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO public.user_metadata (id)
    VALUES (NEW.id)
    ON CONFLICT (id) DO NOTHING;

    RETURN NEW;
END;
$$;
