-- Run this once in the Supabase SQL Editor before running seed_data.py
-- Updated to match actual contractors table schema.

CREATE OR REPLACE FUNCTION public.seed_insert_contractor(
    p_user_id           UUID,
    p_business_name     TEXT,
    p_postcode          TEXT,
    p_phone             TEXT,
    p_expertise         TEXT[],
    p_license_number    TEXT DEFAULT NULL,
    p_insurance_details TEXT DEFAULT NULL
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_id UUID;
BEGIN
    INSERT INTO public.contractors (user_id, business_name, postcode, phone, expertise, license_number, insurance_details)
    VALUES (p_user_id, p_business_name, p_postcode, p_phone, p_expertise, p_license_number, p_insurance_details)
    ON CONFLICT (user_id) DO UPDATE SET business_name = EXCLUDED.business_name
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;
