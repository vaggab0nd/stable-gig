-- Migration 014: Web Push notification subscriptions
--
-- Adds:
--   push_subscriptions — stores contractor Web Push subscription objects
--                        (endpoint + ECDH public key + auth secret).
--
-- The backend sends push notifications when a new job whose activity matches
-- a contractor's registered activities transitions to 'open'.
--
-- VAPID keys (vapid_private_key / vapid_public_key) are stored as env vars /
-- GCP secrets — not in the database.

CREATE TABLE IF NOT EXISTS push_subscriptions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  endpoint    TEXT NOT NULL,
  p256dh      TEXT NOT NULL,   -- ECDH public key (base64url)
  auth_key    TEXT NOT NULL,   -- auth secret (base64url)
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- One subscription record per (user, endpoint) pair.
  -- A user may have multiple subscriptions (e.g. phone + laptop browser).
  UNIQUE (user_id, endpoint)
);

-- Indexes
CREATE INDEX IF NOT EXISTS push_subscriptions_user_id_idx ON push_subscriptions (user_id);

-- RLS: users manage only their own subscriptions; backend writes via service role.
ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "user_read_own_subscriptions" ON push_subscriptions
  FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "user_delete_own_subscriptions" ON push_subscriptions
  FOR DELETE USING (user_id = auth.uid());
