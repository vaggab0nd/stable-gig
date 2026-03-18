-- ============================================================
-- Migration 007: Quality sub-rating + Private Feedback field
--
-- Changes:
--   1. Rename rating_accuracy → rating_quality
--      (schema now matches: Cleanliness · Communication · Quality)
--   2. Regenerate the overall 'rating' computed column
--      (must be dropped and re-added because it references the
--      renamed column by name in a GENERATED ALWAYS expression)
--   3. Add private_feedback TEXT — admin-visible only
--      (excluded from visible_reviews; accessible via service role)
--   4. Recreate visible_reviews to reflect the renamed column
--      and to guarantee private_feedback is never exposed
--   5. Update rating helpers (use AVG on generated column — no
--      change needed in logic, but recreate to pick up new sig)
-- ============================================================


-- ── 1. Drop the generated rating column before rename ────────────
--
-- PostgreSQL allows renaming columns referenced in GENERATED
-- expressions in 15+, but to stay compatible with 14 we drop the
-- generated column first, rename, then re-add it.

ALTER TABLE public.reviews
    DROP COLUMN IF EXISTS rating;


-- ── 2. Rename accuracy → quality ────────────────────────────────

ALTER TABLE public.reviews
    RENAME COLUMN rating_accuracy TO rating_quality;

COMMENT ON COLUMN public.reviews.rating_quality IS
    '1–5: quality of work delivered (client→contractor) or quality of the job brief (contractor→client).';


-- ── 3. Re-add the generated overall rating ───────────────────────
--
-- Always equal to avg(cleanliness, communication, quality), rounded
-- to 2 decimal places.  This column is read-only (GENERATED ALWAYS).

ALTER TABLE public.reviews
    ADD COLUMN rating NUMERIC(3,2)
        GENERATED ALWAYS AS (
            ROUND(
                (rating_cleanliness::numeric
                 + rating_communication::numeric
                 + rating_quality::numeric) / 3,
                2
            )
        ) STORED;

COMMENT ON COLUMN public.reviews.rating IS
    'Generated: average of rating_cleanliness, rating_communication, rating_quality. Read-only.';


-- ── 4. Add private_feedback ───────────────────────────────────────
--
-- Freeform text visible ONLY to platform admins (service role).
-- It is deliberately excluded from visible_reviews so no regular
-- RLS policy can ever expose it.  The TradesmanRating component
-- sends it in the INSERT payload; admins read directly from the
-- raw reviews table via the service role or an admin-only view.

ALTER TABLE public.reviews
    ADD COLUMN IF NOT EXISTS private_feedback TEXT;

COMMENT ON COLUMN public.reviews.private_feedback IS
    'Admin-only field. Never returned by visible_reviews. Accessible only via service role / admin API.';


-- ── 5. RLS guard for private_feedback ───────────────────────────
--
-- Belt-and-braces: even if someone queries the raw reviews table
-- (not the view) with a regular JWT, they cannot read this field
-- because the existing SELECT policies only allow reading own
-- submissions and revealed reviews — not unrestricted table access.
-- No additional policy is needed; the service-role key bypasses RLS
-- for admin tooling.


-- ── 6. Recreate visible_reviews ──────────────────────────────────
--
-- • Renames accuracy column to quality in the projection
-- • Explicitly omits private_feedback (not a SELECT * view)
-- • ai_pros_cons remains hidden until revealed (double-blind)

DROP VIEW IF EXISTS public.visible_reviews;

CREATE VIEW public.visible_reviews AS
SELECT
    id,
    job_id,
    reviewer_id,
    reviewee_id,
    reviewer_role,
    reviewee_role,

    -- Categorical sub-ratings (always visible once the review exists)
    rating_cleanliness,
    rating_communication,
    rating_quality,

    -- Generated overall average
    rating,

    -- Double-blind fields: NULL until both sides reviewed or 14 days passed
    CASE
        WHEN content_visible OR reveal_at <= NOW() THEN body
        ELSE NULL
    END AS body,

    CASE
        WHEN content_visible OR reveal_at <= NOW() THEN ai_pros_cons
        ELSE NULL
    END AS ai_pros_cons,

    -- private_feedback intentionally omitted — never exposed via this view

    content_visible OR (reveal_at <= NOW()) AS is_revealed,
    submitted_at,
    reveal_at
FROM public.reviews;

COMMENT ON VIEW public.visible_reviews IS
    'Double-blind enforced. body / ai_pros_cons hidden until revealed. private_feedback excluded entirely.';


-- ── 7. Refresh aggregate helpers ────────────────────────────────
--
-- No logic change needed — they still call AVG(rating).
-- Recreating ensures the function signature matches the new type.

CREATE OR REPLACE FUNCTION public.contractor_rating(p_contractor_id UUID)
RETURNS NUMERIC
LANGUAGE sql
STABLE
AS $$
    SELECT ROUND(AVG(rating)::NUMERIC, 2)
    FROM   public.reviews
    WHERE  reviewee_id   = p_contractor_id
      AND  reviewee_role = 'contractor'
      AND  (content_visible OR reveal_at <= NOW());
$$;

CREATE OR REPLACE FUNCTION public.client_rating(p_client_id UUID)
RETURNS NUMERIC
LANGUAGE sql
STABLE
AS $$
    SELECT ROUND(AVG(rating)::NUMERIC, 2)
    FROM   public.reviews
    WHERE  reviewee_id   = p_client_id
      AND  reviewee_role = 'client'
      AND  (content_visible OR reveal_at <= NOW());
$$;
