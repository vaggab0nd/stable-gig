/**
 * Supabase Edge Function: contractors
 *
 * CRUD for contractor onboarding following the "Clean Split" schema:
 *
 *   profiles (auth, migration 001)
 *     └─ contractors    — id = auth.users.id  (business identity & activities)
 *          └─ contractor_details  — id = contractors.id  (license / insurance)
 *               └─ bids  — job_id + contractor_id
 *
 * Routes (method + path suffix):
 *   POST   /contractors         → register contractor (id taken from JWT)
 *   GET    /contractors         → fetch own contractor record
 *   PATCH  /contractors         → update contractor fields
 *   POST   /contractors/details → upsert contractor_details
 *   GET    /contractors/details → fetch contractor_details
 *
 * All routes require:  Authorization: Bearer <supabase-jwt>
 *
 * Required secrets (Supabase → Project Settings → Edge Functions → Secrets):
 *   SUPABASE_URL
 *   SUPABASE_ANON_KEY
 */

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { z } from "https://deno.land/x/zod@v3.23.8/mod.ts";

// ── Canonical activity list ───────────────────────────────────────────────────

export const ACTIVITIES = [
  "plumbing",
  "electrical",
  "structural",
  "damp",
  "roofing",
  "carpentry",
  "painting",
  "tiling",
  "flooring",
  "heating_hvac",
  "glazing",
  "landscaping",
  "general",
] as const;

export type Activity = (typeof ACTIVITIES)[number];

// ── Zod schemas ───────────────────────────────────────────────────────────────

/**
 * Core contractor fields.
 * `id` is NOT included in the write payload — it is derived from the JWT.
 */
export const ContractorUpsertSchema = z.object({
  business_name: z
    .string()
    .min(1, "Business name is required")
    .max(200, "Business name must be 200 characters or fewer"),
  postcode: z
    .string()
    .min(1, "Postcode is required")
    .max(20, "Postcode must be 20 characters or fewer"),
  phone: z
    .string()
    .regex(
      /^\+?[\d\s\-().]{7,25}$/,
      "Phone must be 7–25 characters (digits, spaces, +, -, parentheses)",
    ),
  activities: z
    .array(z.enum(ACTIVITIES))
    .min(1, "Select at least one activity")
    .max(ACTIVITIES.length, `Cannot exceed ${ACTIVITIES.length} activities`),
});

export type ContractorUpsert = z.infer<typeof ContractorUpsertSchema>;

/** Full contractor row returned to callers (id = the auth user's UUID). */
export const ContractorResponseSchema = ContractorUpsertSchema.extend({
  id: z.string().uuid(),
  created_at: z.string().datetime(),
});

export type ContractorResponse = z.infer<typeof ContractorResponseSchema>;

/**
 * contractor_details write payload.
 * All fields are optional so callers can update a single field at a time.
 */
export const ContractorDetailsUpsertSchema = z.object({
  license_number: z.string().max(100).optional().nullable(),
  insurance_verified: z.boolean().optional(),
  years_experience: z
    .number()
    .int("Must be a whole number")
    .min(0, "Cannot be negative")
    .max(99, "Must be 99 or fewer")
    .optional()
    .nullable(),
});

export type ContractorDetailsUpsert = z.infer<
  typeof ContractorDetailsUpsertSchema
>;

/** Full contractor_details row returned to callers. */
export const ContractorDetailsResponseSchema =
  ContractorDetailsUpsertSchema.extend({
    id: z.string().uuid(),
    updated_at: z.string().datetime(),
  });

export type ContractorDetailsResponse = z.infer<
  typeof ContractorDetailsResponseSchema
>;

// ── CORS ──────────────────────────────────────────────────────────────────────

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}

function errorResponse(message: string, status: number): Response {
  return jsonResponse({ error: message }, status);
}

// ── ContractorService ─────────────────────────────────────────────────────────

/**
 * Service layer for contractor CRUD.
 *
 * Clean Split invariant: the contractor's `id` equals the auth user's UUID
 * (mirroring profiles.id), so every query uses `.eq("id", this.userId)`.
 * There is no separate `user_id` column.
 */
export class ContractorService {
  constructor(
    private readonly db: ReturnType<typeof createClient>,
    /** The authenticated user's UUID — also the contractors.id PK. */
    private readonly userId: string,
  ) {}

  // ── contractors ────────────────────────────────────────────

  /** Register (insert) a contractor row.  id is set from the JWT. */
  async register(payload: ContractorUpsert): Promise<ContractorResponse> {
    const { data, error } = await this.db
      .from("contractors")
      .insert({ id: this.userId, ...payload })
      .select()
      .single();

    if (error) throw new Error(error.message);
    return ContractorResponseSchema.parse(data);
  }

  /** Fetch the contractor record for the current user. */
  async get(): Promise<ContractorResponse | null> {
    const { data, error } = await this.db
      .from("contractors")
      .select("*")
      .eq("id", this.userId)
      .maybeSingle();

    if (error) throw new Error(error.message);
    if (!data) return null;
    return ContractorResponseSchema.parse(data);
  }

  /** Partially update the contractor record for the current user. */
  async update(payload: Partial<ContractorUpsert>): Promise<ContractorResponse> {
    const { data, error } = await this.db
      .from("contractors")
      .update(payload)
      .eq("id", this.userId)
      .select()
      .single();

    if (error) throw new Error(error.message);
    return ContractorResponseSchema.parse(data);
  }

  // ── contractor_details ─────────────────────────────────────

  /** Upsert contractor_details (id shared with contractors row). */
  async upsertDetails(
    payload: ContractorDetailsUpsert,
  ): Promise<ContractorDetailsResponse> {
    const { data, error } = await this.db
      .from("contractor_details")
      .upsert(
        { id: this.userId, ...payload },
        { onConflict: "id", ignoreDuplicates: false },
      )
      .select()
      .single();

    if (error) throw new Error(error.message);
    return ContractorDetailsResponseSchema.parse(data);
  }

  /** Fetch contractor_details for the current user. */
  async getDetails(): Promise<ContractorDetailsResponse | null> {
    const { data, error } = await this.db
      .from("contractor_details")
      .select("*")
      .eq("id", this.userId)
      .maybeSingle();

    if (error) throw new Error(error.message);
    if (!data) return null;
    return ContractorDetailsResponseSchema.parse(data);
  }
}

// ── Edge Function handler ─────────────────────────────────────────────────────

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: CORS_HEADERS });
  }

  // ── Auth ──────────────────────────────────────────────────────────────────
  const authHeader = req.headers.get("Authorization");
  if (!authHeader?.startsWith("Bearer ")) {
    return errorResponse("Missing or malformed Authorization header", 401);
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY");
  if (!supabaseUrl || !anonKey) {
    return errorResponse(
      "Server misconfiguration: missing Supabase env vars",
      500,
    );
  }

  // User-scoped client — all queries run under RLS
  const db = createClient(supabaseUrl, anonKey, {
    global: { headers: { Authorization: authHeader } },
  });

  const {
    data: { user },
    error: authError,
  } = await db.auth.getUser();

  if (authError || !user) {
    return errorResponse("Invalid or expired token", 401);
  }

  const service = new ContractorService(db, user.id);
  const { pathname } = new URL(req.url);

  // ── Route: /contractors/details ───────────────────────────────────────────
  if (pathname.endsWith("/contractors/details")) {
    try {
      if (req.method === "GET") {
        const details = await service.getDetails();
        // Return stub with id so the client always has something to build on
        return jsonResponse(details ?? { id: user.id });
      }

      if (req.method === "POST") {
        const raw = await req.json().catch(() => ({}));
        const parsed = ContractorDetailsUpsertSchema.safeParse(raw);
        if (!parsed.success) {
          return jsonResponse({ errors: parsed.error.flatten() }, 422);
        }
        const details = await service.upsertDetails(parsed.data);
        return jsonResponse(details);
      }

      return errorResponse("Method not allowed", 405);
    } catch (err) {
      return errorResponse(
        err instanceof Error ? err.message : "Internal error",
        500,
      );
    }
  }

  // ── Route: /contractors ───────────────────────────────────────────────────
  if (pathname.endsWith("/contractors")) {
    try {
      if (req.method === "GET") {
        const contractor = await service.get();
        if (!contractor) return jsonResponse(null, 404);
        return jsonResponse(contractor);
      }

      if (req.method === "POST") {
        const raw = await req.json().catch(() => ({}));
        const parsed = ContractorUpsertSchema.safeParse(raw);
        if (!parsed.success) {
          return jsonResponse({ errors: parsed.error.flatten() }, 422);
        }
        const contractor = await service.register(parsed.data);
        return jsonResponse(contractor, 201);
      }

      if (req.method === "PATCH") {
        const raw = await req.json().catch(() => ({}));
        const parsed = ContractorUpsertSchema.partial().safeParse(raw);
        if (!parsed.success) {
          return jsonResponse({ errors: parsed.error.flatten() }, 422);
        }
        const contractor = await service.update(parsed.data);
        return jsonResponse(contractor);
      }

      return errorResponse("Method not allowed", 405);
    } catch (err) {
      return errorResponse(
        err instanceof Error ? err.message : "Internal error",
        500,
      );
    }
  }

  return errorResponse("Not found", 404);
});
