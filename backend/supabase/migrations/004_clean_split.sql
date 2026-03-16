-- ============================================================
-- Migration 004: Clean Split — align contractor tables with the
--                "profiles → contractors → contractor_details"
--                identity chain and rename contractor_profiles
--                to contractor_details.
--
-- This migration rolls back the layout introduced in 003 and
-- re-creates the four tables with the Clean Split design:
--
--   profiles            (auth, migration 001 — unchanged)
--     └─ contractors    (business identity; id = profiles.id)
--          └─ contractor_details  (heavy/verified data; id = contractors.id)
--               └─ bids (contractor ↔ job link)
--   jobs                (consumer job postings — recreated unchanged)
-- ============================================================

-- ── Tear down migration-003 objects (reverse dependency order) ──

DROP TRIGGER  IF EXISTS on_contractor_created ON public.contractors;
DROP FUNCTION IF EXISTS public.handle_new_contractor();

DROP TABLE IF EXISTS public.bids                CASCADE;
DROP TABLE IF EXISTS public.contractor_profiles CASCADE;   -- old name
DROP TABLE IF EXISTS public.contractor_details  CASCADE;   -- idempotent
DROP TABLE IF EXISTS public.contractors         CASCADE;
DROP TABLE IF EXISTS public.jobs                CASCADE;

-- ── jobs ─────────────────────────────────────────────────────

CREATE TABLE public.jobs (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    title       TEXT        NOT NULL,
    description TEXT        NOT NULL,
    activity    TEXT        NOT NULL
                            CHECK (activity IN (
                                'plumbing', 'electrical', 'structural',
                                'damp', 'roofing', 'carpentry', 'painting',
                                'tiling', 'flooring', 'heating_hvac',
                                'glazing', 'landscaping', 'general'
                            )),
    postcode    TEXT        NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'awarded', 'completed', 'cancelled')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.jobs          IS 'Home-repair jobs posted by consumers.';
COMMENT ON COLUMN public.jobs.activity IS 'Trade category required for the job.';
COMMENT ON COLUMN public.jobs.status   IS 'Lifecycle: open → awarded → completed | cancelled.';

-- ── contractors ──────────────────────────────────────────────
-- id is the same UUID as profiles.id (and therefore auth.users.id).
-- No separate user_id column — the PK *is* the user identity.

CREATE TABLE public.contractors (
    id            UUID    PRIMARY KEY REFERENCES public.profiles(id) ON DELETE CASCADE,
    business_name TEXT    NOT NULL,
    postcode      TEXT    NOT NULL,
    phone         TEXT    NOT NULL,
    -- Subset of the canonical activity list, e.g. '{"plumbing","roofing"}'
    activities    TEXT[]  NOT NULL DEFAULT '{}'
                          CHECK (cardinality(activities) > 0),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.contractors            IS 'Contractor business identity; id mirrors profiles.id (and auth.users.id).';
COMMENT ON COLUMN public.contractors.activities IS 'Array of trade categories offered; must contain at least one entry.';

-- ── contractor_details ────────────────────────────────────────
-- Heavy / verification-sensitive data kept in a separate table
-- so the core contractors row stays lean for list queries.

CREATE TABLE public.contractor_details (
    id                  UUID    PRIMARY KEY REFERENCES public.contractors(id) ON DELETE CASCADE,
    license_number      TEXT,
    insurance_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    years_experience    INTEGER CHECK (years_experience >= 0),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.contractor_details                    IS 'Extended contractor data (license, insurance); 1-to-1 with contractors.';
COMMENT ON COLUMN public.contractor_details.insurance_verified IS 'Set TRUE by admin after insurance documents are reviewed.';

-- ── bids ─────────────────────────────────────────────────────

CREATE TABLE public.bids (
    id            UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id        UUID    NOT NULL REFERENCES public.jobs(id)        ON DELETE CASCADE,
    contractor_id UUID    NOT NULL REFERENCES public.contractors(id) ON DELETE CASCADE,
    amount_pence  INTEGER NOT NULL CHECK (amount_pence > 0),
    note          TEXT,
    status        TEXT    NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'accepted', 'rejected')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, contractor_id)
);

COMMENT ON TABLE  public.bids              IS 'Contractor quotes on consumer jobs.';
COMMENT ON COLUMN public.bids.amount_pence IS 'Quote amount in pence (avoids floating-point rounding).';
COMMENT ON COLUMN public.bids.status       IS 'pending → accepted | rejected.';

-- ── Row Level Security ────────────────────────────────────────

ALTER TABLE public.jobs                ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.contractors         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.contractor_details  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bids                ENABLE ROW LEVEL SECURITY;

-- jobs: owners manage their own; registered contractors can read open jobs
CREATE POLICY "jobs: owner full access"
    ON public.jobs FOR ALL
    USING  (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "jobs: contractors read open"
    ON public.jobs FOR SELECT
    USING (
        status = 'open'
        AND auth.uid() IN (SELECT id FROM public.contractors)
    );

-- contractors: each contractor owns their single row (id = auth.uid())
CREATE POLICY "contractors: select own"
    ON public.contractors FOR SELECT
    USING (auth.uid() = id);

CREATE POLICY "contractors: insert own"
    ON public.contractors FOR INSERT
    WITH CHECK (auth.uid() = id);

CREATE POLICY "contractors: update own"
    ON public.contractors FOR UPDATE
    USING (auth.uid() = id);

-- contractor_details: accessible only by the owning contractor
CREATE POLICY "contractor_details: select own"
    ON public.contractor_details FOR SELECT
    USING (auth.uid() = id);

CREATE POLICY "contractor_details: insert own"
    ON public.contractor_details FOR INSERT
    WITH CHECK (auth.uid() = id);

CREATE POLICY "contractor_details: update own"
    ON public.contractor_details FOR UPDATE
    USING (auth.uid() = id);

-- bids: contractors manage their own bids; job owners can read bids on their jobs
CREATE POLICY "bids: contractor full access"
    ON public.bids FOR ALL
    USING  (contractor_id = auth.uid())
    WITH CHECK (contractor_id = auth.uid());

CREATE POLICY "bids: job owner read"
    ON public.bids FOR SELECT
    USING (
        job_id IN (SELECT id FROM public.jobs WHERE user_id = auth.uid())
    );

-- ── Trigger: auto-update updated_at on contractor_details ────

CREATE TRIGGER contractor_details_updated_at
    BEFORE UPDATE ON public.contractor_details
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ── Trigger: auto-create contractor_details row on insert ────

CREATE OR REPLACE FUNCTION public.handle_new_contractor()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.contractor_details (id)
    VALUES (NEW.id)
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE TRIGGER on_contractor_created
    AFTER INSERT ON public.contractors
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_contractor();
