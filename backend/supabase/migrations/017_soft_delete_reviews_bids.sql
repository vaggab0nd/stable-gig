-- Migration 017: Soft-delete audit trail for reviews and bids
-- 
-- Adds soft-delete columns to reviews and bids tables, enabling audit trails
-- and dispute resolution without permanently destroying data.
-- 
-- RLS policies filter out deleted rows automatically, maintaining the same
-- logical behavior while preserving audit history.

-- ============================================================================
-- Review soft-delete columns
-- ============================================================================

ALTER TABLE reviews
ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE NULL DEFAULT NULL,
ADD COLUMN deleted_by_user_id UUID REFERENCES auth.users(id) NULL DEFAULT NULL;

-- Index for efficient filtering in RLS policies
CREATE INDEX idx_reviews_deleted_at ON reviews(deleted_at);

-- ============================================================================
-- Bids soft-delete columns
-- ============================================================================

ALTER TABLE bids
ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE NULL DEFAULT NULL,
ADD COLUMN deleted_by_user_id UUID REFERENCES auth.users(id) NULL DEFAULT NULL;

-- Index for efficient filtering in RLS policies
CREATE INDEX idx_bids_deleted_at ON bids(deleted_at);

-- ============================================================================
-- RLS Policy Updates
-- ============================================================================

-- For reviews: exclude soft-deleted rows from all visibility operations
CREATE POLICY "reviews_exclude_soft_deleted" ON reviews
  FOR SELECT
  USING (deleted_at IS NULL);

-- For bids: exclude soft-deleted rows from all visibility operations
CREATE POLICY "bids_exclude_soft_deleted" ON bids
  FOR SELECT
  USING (deleted_at IS NULL);

-- ============================================================================
-- Admin audit view: see all reviews including deleted ones
-- ============================================================================

CREATE OR REPLACE VIEW reviews_audit AS
SELECT 
  r.id, r.job_id, r.reviewer_id, r.reviewee_id,
  r.reviewer_role, r.reviewee_role,
  r.rating_cleanliness, r.rating_communication, r.rating_quality, r.rating,
  r.body, r.ai_pros_cons, r.private_feedback,
  r.content_visible, r.reveal_at, r.submitted_at,
  r.deleted_at, r.deleted_by_user_id,
  CASE WHEN r.deleted_at IS NOT NULL THEN true ELSE false END AS is_deleted
FROM reviews r
ORDER BY r.submitted_at DESC;

-- Grant access to authenticated users (for dispute resolution workflow)
-- Restrict to specific columns if needed
GRANT SELECT (
  id, job_id, reviewer_id, reviewee_id, reviewer_role, reviewee_role,
  rating_cleanliness, rating_communication, rating_quality, rating,
  body, ai_pros_cons, submitted_at, deleted_at
) ON reviews_audit TO authenticated;

-- ============================================================================
-- Comment: Soft-delete best practices
-- ============================================================================

COMMENT ON COLUMN reviews.deleted_at IS 'Timestamp when the review was soft-deleted. NULL = not deleted.';
COMMENT ON COLUMN reviews.deleted_by_user_id IS 'User who initiated the deletion. Always the reviewer (cannot delete reviews about you).';
COMMENT ON COLUMN bids.deleted_at IS 'Timestamp when the bid was soft-deleted. NULL = not deleted.';
COMMENT ON COLUMN bids.deleted_by_user_id IS 'User who initiated the deletion. The contractor who placed the bid.';
