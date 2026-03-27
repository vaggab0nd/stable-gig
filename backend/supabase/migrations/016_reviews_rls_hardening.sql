-- Migration 016: Harden reviews table RLS
--
-- Problem
-- -------
-- A SELECT policy with USING (true) allows any authenticated user to read
-- every review row, including body text that should still be hidden under
-- the double-blind.  While the column-level REVOKE from migration 008
-- already blocks private_feedback at the PostgreSQL level, the row-level
-- breach still exposes review body content before both parties have
-- submitted, breaking the double-blind guarantee.
--
-- Fix
-- ---
-- 1. Drop any unrestricted SELECT policy on reviews.
-- 2. Drop and recreate the two correct SELECT policies (own submission +
--    revealed about me) so the state is unambiguous regardless of what
--    order policies were applied.
-- 3. Re-assert the column-level REVOKE + selective GRANT from migration
--    008, ensuring private_feedback remains outside all authenticated/anon
--    grants even if a future dashboard action re-adds a broad policy.

-- ── 1. Drop any overly broad SELECT policies ──────────────────────────────────
--
-- Common names Supabase's dashboard uses when auto-generating policies.
-- We also drop the correct policies so step 2 recreates them in a clean state.

DROP POLICY IF EXISTS "Enable read access for all users"        ON public.reviews;
DROP POLICY IF EXISTS "Allow read access for authenticated users" ON public.reviews;
DROP POLICY IF EXISTS "reviews: select own submission"          ON public.reviews;
DROP POLICY IF EXISTS "reviews: select revealed about me"       ON public.reviews;


-- ── 2. Recreate the two correct, narrowly-scoped SELECT policies ──────────────

-- Reviewers can always read their own submission (before and after reveal)
CREATE POLICY "reviews: select own submission"
    ON public.reviews FOR SELECT
    USING (auth.uid() = reviewer_id);

-- Reviewees can read reviews about them only after the double-blind lifts
CREATE POLICY "reviews: select revealed about me"
    ON public.reviews FOR SELECT
    USING (
        auth.uid() = reviewee_id
        AND (content_visible OR reveal_at <= NOW())
    );


-- ── 3. Re-assert column-level security (idempotent) ──────────────────────────
--
-- Belt-and-braces: if any future migration or dashboard action re-grants
-- table-level SELECT, the column-level REVOKE below invalidates it for the
-- authenticated and anon roles.  private_feedback is intentionally absent
-- from the GRANT list.

REVOKE SELECT ON public.reviews FROM authenticated, anon;

GRANT SELECT (
    id,
    job_id,
    reviewer_id,
    reviewee_id,
    reviewer_role,
    reviewee_role,
    rating_cleanliness,
    rating_communication,
    rating_quality,
    rating,
    body,
    ai_pros_cons,
    content_visible,
    reveal_at,
    submitted_at
) ON public.reviews TO authenticated;

-- anon still gets no SELECT grant — the visible_reviews view is the correct
-- public-facing query surface and is already restricted to safe columns.
