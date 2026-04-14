-- Run this once in the Supabase SQL Editor before running seed_data.py
-- It creates a helper function so the seed script can bypass the
-- PostgREST schema cache issue with TEXT[] columns.

CREATE OR REPLACE FUNCTION public.seed_insert_contractor(
    p_id            UUID,
    p_business_name TEXT,
    p_postcode      TEXT,
    p_phone         TEXT,
    p_activities    TEXT[]
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.contractors (id, business_name, postcode, phone, activities)
    VALUES (p_id, p_business_name, p_postcode, p_phone, p_activities)
    ON CONFLICT (id) DO NOTHING;
END;
$$;
