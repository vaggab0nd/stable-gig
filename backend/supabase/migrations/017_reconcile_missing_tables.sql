-- ============================================================
-- Migration 017: Reconcile missing tables
--
-- The live DB was set up manually before migrations were applied.
-- This migration adds the tables that were never created, using
-- schemas compatible with the actual contractors/reviews layout
-- (user_id on contractors, expertise not activities).
--
-- Safe to run multiple times — all statements use IF NOT EXISTS.
-- Does NOT alter or drop any existing table.
-- ============================================================


-- ── 1. contractor_details ─────────────────────────────────────
-- 1-to-1 with contractors.id (auto-generated UUID).
-- Holds verification data, AI summary, embeddings, Stripe ID.

CREATE TABLE IF NOT EXISTS public.contractor_details (
    id                  UUID        PRIMARY KEY REFERENCES public.contractors(id) ON DELETE CASCADE,
    insurance_verified  BOOLEAN     NOT NULL DEFAULT FALSE,
    years_experience    INTEGER     CHECK (years_experience >= 0),
    ai_review_summary   JSONB,
    profile_text        TEXT,
    stripe_account_id   TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.contractor_details IS
    'Extended contractor data; 1-to-1 with contractors.id.';

ALTER TABLE public.contractor_details ENABLE ROW LEVEL SECURITY;

CREATE POLICY "contractor_details: select own"
    ON public.contractor_details FOR SELECT
    USING (id IN (SELECT id FROM public.contractors WHERE user_id = auth.uid()));

CREATE POLICY "contractor_details: insert own"
    ON public.contractor_details FOR INSERT
    WITH CHECK (id IN (SELECT id FROM public.contractors WHERE user_id = auth.uid()));

CREATE POLICY "contractor_details: update own"
    ON public.contractor_details FOR UPDATE
    USING (id IN (SELECT id FROM public.contractors WHERE user_id = auth.uid()));

-- Ensure updated_at stays current
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;

DROP TRIGGER IF EXISTS contractor_details_updated_at ON public.contractor_details;
CREATE TRIGGER contractor_details_updated_at
    BEFORE UPDATE ON public.contractor_details
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Auto-create a contractor_details row whenever a contractor is inserted
CREATE OR REPLACE FUNCTION public.handle_new_contractor()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    INSERT INTO public.contractor_details (id) VALUES (NEW.id) ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_contractor_created ON public.contractors;
CREATE TRIGGER on_contractor_created
    AFTER INSERT ON public.contractors
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_contractor();

-- Backfill contractor_details for contractors that already exist
INSERT INTO public.contractor_details (id)
SELECT id FROM public.contractors
ON CONFLICT (id) DO NOTHING;


-- ── 2. job_questions ──────────────────────────────────────────
-- Anonymous contractor Q&A per job.
-- contractor_id = contractors.id (auto-generated), not auth.users.id

CREATE TABLE IF NOT EXISTS public.job_questions (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id        UUID        NOT NULL REFERENCES public.jobs(id) ON DELETE CASCADE,
    contractor_id UUID        NOT NULL,
    question      TEXT        NOT NULL CHECK (length(question) BETWEEN 10 AND 1000),
    answer        TEXT        CHECK (answer IS NULL OR length(answer) BETWEEN 1 AND 2000),
    answered_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS job_questions_job_id_idx        ON public.job_questions (job_id);
CREATE INDEX IF NOT EXISTS job_questions_contractor_id_idx ON public.job_questions (contractor_id);

ALTER TABLE public.job_questions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "homeowner_read_job_questions" ON public.job_questions
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM public.jobs WHERE jobs.id = job_questions.job_id AND jobs.user_id = auth.uid())
    );

CREATE POLICY "contractor_read_own_questions" ON public.job_questions
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM public.contractors WHERE contractors.id = job_questions.contractor_id AND contractors.user_id = auth.uid())
    );

CREATE POLICY "contractor_insert_questions" ON public.job_questions
    FOR INSERT WITH CHECK (
        EXISTS (SELECT 1 FROM public.contractors WHERE contractors.id = job_questions.contractor_id AND contractors.user_id = auth.uid())
    );

CREATE POLICY "homeowner_answer_questions" ON public.job_questions
    FOR UPDATE USING (
        EXISTS (SELECT 1 FROM public.jobs WHERE jobs.id = job_questions.job_id AND jobs.user_id = auth.uid())
    );


-- ── 3. push_subscriptions ────────────────────────────────────
-- Web Push subscriptions (one per user+endpoint pair).

CREATE TABLE IF NOT EXISTS public.push_subscriptions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    endpoint    TEXT        NOT NULL,
    p256dh      TEXT        NOT NULL,
    auth_key    TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, endpoint)
);

CREATE INDEX IF NOT EXISTS push_subscriptions_user_id_idx ON public.push_subscriptions (user_id);

ALTER TABLE public.push_subscriptions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "user_read_own_subscriptions" ON public.push_subscriptions
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "user_delete_own_subscriptions" ON public.push_subscriptions
    FOR DELETE USING (user_id = auth.uid());


-- ── 4. job_milestones ────────────────────────────────────────
-- Homeowner-defined checkpoints; contractor submits photo evidence.

CREATE TABLE IF NOT EXISTS public.job_milestones (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id      UUID        NOT NULL REFERENCES public.jobs(id) ON DELETE CASCADE,
    title       TEXT        NOT NULL CHECK (length(title) BETWEEN 3 AND 200),
    description TEXT,
    order_index SMALLINT    NOT NULL DEFAULT 0,
    status      TEXT        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'submitted', 'approved', 'rejected')),
    approved_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS job_milestones_job_id_idx ON public.job_milestones (job_id);

ALTER TABLE public.job_milestones ENABLE ROW LEVEL SECURITY;

CREATE POLICY "homeowner_read_milestones" ON public.job_milestones
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM public.jobs WHERE jobs.id = job_milestones.job_id AND jobs.user_id = auth.uid())
    );

CREATE POLICY "contractor_read_milestones" ON public.job_milestones
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM public.bids
            WHERE bids.job_id = job_milestones.job_id AND bids.status = 'accepted'
            AND EXISTS (SELECT 1 FROM public.contractors WHERE contractors.id = bids.contractor_id AND contractors.user_id = auth.uid())
        )
    );

CREATE POLICY "homeowner_manage_milestones" ON public.job_milestones
    FOR ALL USING (
        EXISTS (SELECT 1 FROM public.jobs WHERE jobs.id = job_milestones.job_id AND jobs.user_id = auth.uid())
    );


-- ── 5. milestone_photos ──────────────────────────────────────
-- Photos uploaded by the contractor as milestone evidence.

CREATE TABLE IF NOT EXISTS public.milestone_photos (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    milestone_id UUID        NOT NULL REFERENCES public.job_milestones(id) ON DELETE CASCADE,
    job_id       UUID        NOT NULL REFERENCES public.jobs(id)           ON DELETE CASCADE,
    uploaded_by  UUID        NOT NULL REFERENCES auth.users(id),
    image_source TEXT        NOT NULL,
    note         TEXT        CHECK (note IS NULL OR length(note) <= 500),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS milestone_photos_milestone_id_idx ON public.milestone_photos (milestone_id);

ALTER TABLE public.milestone_photos ENABLE ROW LEVEL SECURITY;

CREATE POLICY "homeowner_read_milestone_photos" ON public.milestone_photos
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM public.jobs WHERE jobs.id = milestone_photos.job_id AND jobs.user_id = auth.uid())
    );

CREATE POLICY "contractor_read_milestone_photos" ON public.milestone_photos
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM public.bids
            WHERE bids.job_id = milestone_photos.job_id AND bids.status = 'accepted'
            AND EXISTS (SELECT 1 FROM public.contractors WHERE contractors.id = bids.contractor_id AND contractors.user_id = auth.uid())
        )
    );

CREATE POLICY "contractor_insert_milestone_photos" ON public.milestone_photos
    FOR INSERT WITH CHECK (uploaded_by = auth.uid());
