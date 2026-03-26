-- Migration 013: Anonymous contractor Q&A per job
--
-- Adds:
--   job_questions — contractors ask clarifying questions on open jobs;
--                   homeowners answer; contractor identity is hidden from homeowners.
--
-- Anonymisation strategy: contractor_id is stored but the API layer strips it
-- from homeowner responses, replacing it with a stable per-job ordinal label
-- ("Contractor 1", "Contractor 2" …) based on first-question timestamp order.

CREATE TABLE IF NOT EXISTS job_questions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id        UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  contractor_id UUID NOT NULL,   -- contractors.id (not auth.users.id)
  question      TEXT NOT NULL CHECK (length(question) BETWEEN 10 AND 1000),
  answer        TEXT            CHECK (answer IS NULL OR length(answer) BETWEEN 1 AND 2000),
  answered_at   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS job_questions_job_id_idx        ON job_questions (job_id);
CREATE INDEX IF NOT EXISTS job_questions_contractor_id_idx ON job_questions (contractor_id);

-- RLS
ALTER TABLE job_questions ENABLE ROW LEVEL SECURITY;

-- Homeowner of the job sees all questions on their jobs (via the jobs table)
CREATE POLICY "homeowner_read_job_questions" ON job_questions
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM jobs
       WHERE jobs.id = job_questions.job_id
         AND jobs.user_id = auth.uid()
    )
  );

-- Contractor sees only their own questions
CREATE POLICY "contractor_read_own_questions" ON job_questions
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM contractors
       WHERE contractors.id = job_questions.contractor_id
         AND contractors.user_id = auth.uid()
    )
  );
