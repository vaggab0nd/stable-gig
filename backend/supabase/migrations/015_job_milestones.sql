-- Migration 015: Job milestones and photo evidence
--
-- Adds:
--   job_milestones   — homeowner-defined checkpoints for a job; contractor marks each
--                      as 'submitted' by uploading photo evidence; homeowner approves.
--   milestone_photos — photos submitted by the contractor as evidence of completion.
--
-- Milestone status lifecycle:
--   pending → submitted (contractor uploads photos)
--           → approved  (homeowner approves)  ← terminal
--           → rejected  (homeowner rejects, contractor can re-submit)
--
-- Partial escrow release per milestone is a future enhancement.
-- For now, milestone approval is a soft signal informing the homeowner's
-- decision to call POST /jobs/{id}/escrow/release.

CREATE TABLE IF NOT EXISTS job_milestones (
  id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id      UUID    NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  title       TEXT    NOT NULL CHECK (length(title) BETWEEN 3 AND 200),
  description TEXT,
  order_index SMALLINT NOT NULL DEFAULT 0,
  status      TEXT    NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending', 'submitted', 'approved', 'rejected')),
  approved_at TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS job_milestones_job_id_idx ON job_milestones (job_id);

-- RLS: homeowner and job's contractor can view; only backend writes.
ALTER TABLE job_milestones ENABLE ROW LEVEL SECURITY;

CREATE POLICY "homeowner_read_milestones" ON job_milestones
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM jobs
       WHERE jobs.id = job_milestones.job_id
         AND jobs.user_id = auth.uid()
    )
  );

CREATE POLICY "contractor_read_milestones" ON job_milestones
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM bids
       WHERE bids.job_id  = job_milestones.job_id
         AND bids.status  = 'accepted'
         AND EXISTS (
           SELECT 1 FROM contractors
            WHERE contractors.id      = bids.contractor_id
              AND contractors.user_id = auth.uid()
         )
    )
  );


-- Photo evidence uploaded by the contractor for a milestone
CREATE TABLE IF NOT EXISTS milestone_photos (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  milestone_id UUID NOT NULL REFERENCES job_milestones(id) ON DELETE CASCADE,
  job_id       UUID NOT NULL REFERENCES jobs(id)           ON DELETE CASCADE,
  uploaded_by  UUID NOT NULL REFERENCES auth.users(id),
  image_source TEXT NOT NULL,  -- HTTPS URL or base64 data URI
  note         TEXT CHECK (note IS NULL OR length(note) <= 500),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS milestone_photos_milestone_id_idx ON milestone_photos (milestone_id);

ALTER TABLE milestone_photos ENABLE ROW LEVEL SECURITY;

CREATE POLICY "homeowner_read_milestone_photos" ON milestone_photos
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM jobs
       WHERE jobs.id = milestone_photos.job_id
         AND jobs.user_id = auth.uid()
    )
  );

CREATE POLICY "contractor_read_milestone_photos" ON milestone_photos
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM bids
       WHERE bids.job_id  = milestone_photos.job_id
         AND bids.status  = 'accepted'
         AND EXISTS (
           SELECT 1 FROM contractors
            WHERE contractors.id      = bids.contractor_id
              AND contractors.user_id = auth.uid()
         )
    )
  );
