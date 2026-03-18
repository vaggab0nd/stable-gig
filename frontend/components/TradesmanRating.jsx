/**
 * TradesmanRating
 *
 * React component for the marketplace review flow.
 *
 * ── Features ────────────────────────────────────────────────────────────────
 *  • 5-star rating for three dimensions: Quality, Communication, Cleanliness
 *  • Overall rating auto-computed (matches the DB GENERATED column)
 *  • Freeform feedback textarea
 *  • Hidden "Private Feedback" field — sent to reviews.private_feedback,
 *    never returned by visible_reviews, visible only to platform admins
 *  • Submit button is DISABLED unless escrow_status is 'released' or
 *    'funds_released' (both accepted for forward/backward compatibility)
 *  • Structured INSERT to the Supabase `reviews` table via PostgREST
 *
 * ── Required peer dependencies ────────────────────────────────────────────
 *   react ^18, @supabase/supabase-js ^2
 *
 * ── Usage ────────────────────────────────────────────────────────────────
 *
 *   import { createClient } from '@supabase/supabase-js'
 *   import TradesmanRating from './components/TradesmanRating'
 *
 *   const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)
 *
 *   <TradesmanRating
 *     supabase={supabase}
 *     jobId="job-uuid"
 *     reviewerId="current-user-uuid"
 *     revieweeId="tradesman-uuid"
 *     reviewerRole="client"
 *     revieweeRole="contractor"
 *     revieweeName="Dave's Plumbing"
 *     escrowStatus="funds_released"    // from your payment layer
 *     onSuccess={(review) => console.log('saved', review)}
 *   />
 *
 * ── Supabase table structure required ────────────────────────────────────
 *   See: backend/supabase/migrations/005–007 for the full schema.
 *   Key columns sent by this component:
 *     job_id, reviewer_id, reviewee_id,
 *     reviewer_role, reviewee_role,
 *     rating_quality, rating_communication, rating_cleanliness,
 *     body, private_feedback
 */

import React, { useState, useCallback, useMemo } from 'react'

// ── Constants ─────────────────────────────────────────────────────────────────

/** Both values are accepted; DB stores 'funds_released'. */
const ESCROW_RELEASED_VALUES = new Set(['released', 'funds_released'])

const DIMENSIONS = [
  {
    key: 'quality',
    label: 'Quality',
    hint: 'How good was the overall standard of work?',
    icon: '🔧',
  },
  {
    key: 'communication',
    label: 'Communication',
    hint: 'How clearly did they communicate throughout?',
    icon: '💬',
  },
  {
    key: 'cleanliness',
    label: 'Cleanliness',
    hint: 'How clean did they leave the work area?',
    icon: '✨',
  },
]

const STAR_LABELS = ['', 'Poor', 'Below average', 'Average', 'Good', 'Excellent']

// ── Sub-component: StarRow ────────────────────────────────────────────────────

function StarRow({ dimension, value, onChange, disabled }) {
  const [hovered, setHovered] = useState(0)

  const display = hovered || value

  return (
    <div style={styles.starRow}>
      <div style={styles.dimensionInfo}>
        <span style={styles.dimensionIcon}>{dimension.icon}</span>
        <div>
          <div style={styles.dimensionLabel}>{dimension.label}</div>
          <div style={styles.dimensionHint}>{dimension.hint}</div>
        </div>
      </div>

      <div
        style={styles.starsWrap}
        onMouseLeave={() => !disabled && setHovered(0)}
        role="radiogroup"
        aria-label={`${dimension.label} rating`}
      >
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            type="button"
            disabled={disabled}
            aria-label={`${n} star${n > 1 ? 's' : ''} — ${STAR_LABELS[n]}`}
            aria-pressed={value === n}
            style={{
              ...styles.starBtn,
              color: n <= display ? '#f59e0b' : '#d1d5db',
              transform: n <= display ? 'scale(1.15)' : 'scale(1)',
              cursor: disabled ? 'not-allowed' : 'pointer',
            }}
            onMouseEnter={() => !disabled && setHovered(n)}
            onClick={() => !disabled && onChange(n)}
          >
            ★
          </button>
        ))}

        <span style={styles.starLabel}>
          {display ? STAR_LABELS[display] : <span style={{ color: '#9ca3af' }}>—</span>}
        </span>
      </div>
    </div>
  )
}

// ── Sub-component: OverallBadge ───────────────────────────────────────────────

function OverallBadge({ overall }) {
  if (overall === null) return null

  const color =
    overall >= 4.5 ? '#059669'
    : overall >= 3.5 ? '#2563eb'
    : overall >= 2.5 ? '#d97706'
    : '#dc2626'

  return (
    <div style={{ ...styles.overallBadge, borderColor: color, color }}>
      <span style={styles.overallNum}>{overall.toFixed(1)}</span>
      <span style={styles.overallLabel}>/ 5.0 overall</span>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function TradesmanRating({
  supabase,            // pre-initialised @supabase/supabase-js client
  jobId,
  reviewerId,
  revieweeId,
  reviewerRole = 'client',
  revieweeRole = 'contractor',
  revieweeName = 'the tradesman',
  escrowStatus,        // string from your payment/escrow layer
  onSuccess,           // (reviewRow) => void
  onError,             // (error) => void
}) {
  // ── Form state ─────────────────────────────────────────────────

  const [ratings, setRatings] = useState({
    quality:       0,
    communication: 0,
    cleanliness:   0,
  })
  const [body,            setBody]            = useState('')
  const [privateFeedback, setPrivateFeedback] = useState('')
  const [status,          setStatus]          = useState('idle')
  // 'idle' | 'submitting' | 'success' | 'error'
  const [errorMsg,        setErrorMsg]        = useState('')
  const [savedReview,     setSavedReview]     = useState(null)

  // ── Derived values ──────────────────────────────────────────────

  const escrowReleased = ESCROW_RELEASED_VALUES.has(escrowStatus)

  const allRated = ratings.quality > 0 && ratings.communication > 0 && ratings.cleanliness > 0

  const overall = useMemo(() => {
    if (!allRated) return null
    return (ratings.quality + ratings.communication + ratings.cleanliness) / 3
  }, [ratings, allRated])

  const canSubmit = escrowReleased && allRated && status === 'idle'

  // ── Handlers ────────────────────────────────────────────────────

  const handleRating = useCallback((key, val) => {
    setRatings((prev) => ({ ...prev, [key]: val }))
  }, [])

  const handleSubmit = useCallback(
    async (e) => {
      e.preventDefault()
      if (!canSubmit) return

      setStatus('submitting')
      setErrorMsg('')

      const payload = {
        job_id:               jobId,
        reviewer_id:          reviewerId,
        reviewee_id:          revieweeId,
        reviewer_role:        reviewerRole,
        reviewee_role:        revieweeRole,
        rating_quality:       ratings.quality,
        rating_communication: ratings.communication,
        rating_cleanliness:   ratings.cleanliness,
        body:                 body.trim() || null,
        // private_feedback is only sent if non-empty to keep the payload clean
        ...(privateFeedback.trim() && { private_feedback: privateFeedback.trim() }),
      }

      const { data, error } = await supabase
        .from('reviews')
        .insert(payload)
        .select()
        .single()

      if (error) {
        const msg = error.message ?? 'Submission failed. Please try again.'
        setErrorMsg(msg)
        setStatus('error')
        onError?.(error)
        return
      }

      setSavedReview(data)
      setStatus('success')
      onSuccess?.(data)
    },
    [
      canSubmit, jobId, reviewerId, revieweeId,
      reviewerRole, revieweeRole, ratings, body,
      privateFeedback, supabase, onSuccess, onError,
    ],
  )

  // ── Render: locked state ────────────────────────────────────────

  if (!escrowReleased) {
    return (
      <div style={styles.card}>
        <div style={styles.lockedWrap}>
          <div style={styles.lockIcon}>🔒</div>
          <h3 style={styles.lockedTitle}>Reviews locked</h3>
          <p style={styles.lockedBody}>
            You can leave a review once payment has been released from escrow.
          </p>
          <div style={styles.escrowPill}>
            Escrow status: <strong>{escrowStatus ?? 'pending'}</strong>
          </div>
        </div>
      </div>
    )
  }

  // ── Render: success state ───────────────────────────────────────

  if (status === 'success') {
    return (
      <div style={styles.card}>
        <div style={styles.successWrap}>
          <div style={styles.successIcon}>✓</div>
          <h3 style={styles.successTitle}>Review submitted</h3>
          <p style={styles.successBody}>
            Your review of <strong>{revieweeName}</strong> has been saved. It will
            be published once they leave their review, or automatically after 14 days.
          </p>
          {savedReview && (
            <OverallBadge overall={
              (savedReview.rating_quality + savedReview.rating_communication + savedReview.rating_cleanliness) / 3
            } />
          )}
        </div>
      </div>
    )
  }

  // ── Render: form ────────────────────────────────────────────────

  return (
    <div style={styles.card}>
      {/* Header */}
      <div style={styles.header}>
        <h2 style={styles.title}>Rate {revieweeName}</h2>
        <p style={styles.subtitle}>
          Your review is private until {revieweeName} submits theirs,
          or 14 days pass.
        </p>
      </div>

      <form onSubmit={handleSubmit} noValidate>

        {/* ── Star ratings ─────────────────────────────────────── */}
        <fieldset style={styles.fieldset}>
          <legend style={styles.legend}>
            Rate each area&nbsp;
            <span style={styles.legendHint}>(tap a star to score 1–5)</span>
          </legend>

          {DIMENSIONS.map((dim) => (
            <StarRow
              key={dim.key}
              dimension={dim}
              value={ratings[dim.key]}
              onChange={(val) => handleRating(dim.key, val)}
              disabled={status === 'submitting'}
            />
          ))}

          {/* Overall badge updates live */}
          <div style={styles.overallRow}>
            <span style={styles.overallPrompt}>
              {allRated ? 'Overall score' : 'Rate all three areas to see your overall score'}
            </span>
            <OverallBadge overall={overall} />
          </div>
        </fieldset>

        {/* ── Public feedback textarea ─────────────────────────── */}
        <div style={styles.field}>
          <label htmlFor="tr-body" style={styles.label}>
            Written feedback{' '}
            <span style={styles.optTag}>optional</span>
          </label>
          <textarea
            id="tr-body"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            disabled={status === 'submitting'}
            maxLength={2000}
            rows={4}
            placeholder="Describe your experience in your own words…"
            style={styles.textarea}
          />
          <div style={styles.charCount}>{body.length} / 2000</div>
        </div>

        {/* ── Private feedback (admin-only) ────────────────────── */}
        <div style={{ ...styles.field, ...styles.privateField }}>
          <div style={styles.privateHeader}>
            <span style={styles.privateIcon}>🔐</span>
            <label htmlFor="tr-private" style={styles.privateLabel}>
              Private feedback
            </label>
            <span style={styles.privateBadge}>Admin only — not shown to the tradesman</span>
          </div>
          <p style={styles.privateHint}>
            Use this for concerns you want the platform team to review (e.g. conduct
            issues, safety concerns, payment disputes). This is never visible to{' '}
            {revieweeName}.
          </p>
          <textarea
            id="tr-private"
            value={privateFeedback}
            onChange={(e) => setPrivateFeedback(e.target.value)}
            disabled={status === 'submitting'}
            maxLength={2000}
            rows={3}
            placeholder="Anything you'd like to flag to the platform team…"
            style={{ ...styles.textarea, ...styles.privateTextarea }}
          />
          <div style={styles.charCount}>{privateFeedback.length} / 2000</div>
        </div>

        {/* ── Error banner ─────────────────────────────────────── */}
        {status === 'error' && (
          <div style={styles.errorBanner} role="alert">
            <strong>Error: </strong>{errorMsg}
            <button
              type="button"
              style={styles.retryBtn}
              onClick={() => setStatus('idle')}
            >
              Try again
            </button>
          </div>
        )}

        {/* ── Submit ───────────────────────────────────────────── */}
        <button
          type="submit"
          disabled={!canSubmit}
          style={{
            ...styles.submitBtn,
            ...(canSubmit ? {} : styles.submitBtnDisabled),
          }}
          aria-disabled={!canSubmit}
          title={
            !escrowReleased
              ? 'Waiting for escrow to be released'
              : !allRated
              ? 'Please rate all three areas'
              : undefined
          }
        >
          {status === 'submitting' ? (
            <span style={styles.submitInner}>
              <span style={styles.spinner} />
              Submitting…
            </span>
          ) : (
            'Submit review'
          )}
        </button>

        {!allRated && escrowReleased && (
          <p style={styles.validationNote}>
            Please rate Quality, Communication, and Cleanliness before submitting.
          </p>
        )}
      </form>
    </div>
  )
}

// ── Styles (plain objects — no CSS-in-JS dependency) ─────────────────────────
//
// All values use system fonts and a neutral palette that matches the existing
// frontend/index.html design language.  Override via className / CSS modules
// as needed in your React build.

const styles = {
  card: {
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    maxWidth: 560,
    margin: '0 auto',
    background: '#fff',
    border: '1px solid #e5e7eb',
    borderRadius: 12,
    padding: '1.5rem',
    color: '#111',
    boxSizing: 'border-box',
  },

  // ── Header ──────────────────────────────────────────────────
  header: { marginBottom: '1.5rem' },
  title:  { fontSize: '1.2rem', fontWeight: 700, margin: 0, marginBottom: '0.25rem' },
  subtitle: { fontSize: '0.82rem', color: '#6b7280', margin: 0 },

  // ── Star ratings ────────────────────────────────────────────
  fieldset: {
    border: '1px solid #e5e7eb',
    borderRadius: 8,
    padding: '1rem',
    marginBottom: '1.25rem',
  },
  legend: {
    fontSize: '0.8rem',
    fontWeight: 600,
    color: '#374151',
    padding: '0 0.25rem',
  },
  legendHint: { fontWeight: 400, color: '#9ca3af' },

  starRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0.65rem 0',
    borderBottom: '1px solid #f3f4f6',
    gap: '0.75rem',
    flexWrap: 'wrap',
  },
  dimensionInfo: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    flex: 1,
    minWidth: 160,
  },
  dimensionIcon:  { fontSize: '1.2rem' },
  dimensionLabel: { fontWeight: 600, fontSize: '0.9rem' },
  dimensionHint:  { fontSize: '0.73rem', color: '#9ca3af', marginTop: 2 },

  starsWrap: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.05rem',
    flexShrink: 0,
  },
  starBtn: {
    background: 'none',
    border: 'none',
    padding: '0.1rem 0.15rem',
    fontSize: '1.6rem',
    lineHeight: 1,
    transition: 'color 0.1s, transform 0.1s',
  },
  starLabel: {
    marginLeft: '0.5rem',
    fontSize: '0.78rem',
    color: '#374151',
    minWidth: 90,
  },

  overallRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingTop: '0.75rem',
    flexWrap: 'wrap',
    gap: '0.5rem',
  },
  overallPrompt: { fontSize: '0.82rem', color: '#6b7280' },
  overallBadge: {
    display: 'inline-flex',
    alignItems: 'baseline',
    gap: '0.2rem',
    border: '2px solid',
    borderRadius: 8,
    padding: '0.2rem 0.6rem',
    fontWeight: 700,
  },
  overallNum:   { fontSize: '1.2rem' },
  overallLabel: { fontSize: '0.75rem', fontWeight: 400 },

  // ── Text fields ─────────────────────────────────────────────
  field: { marginBottom: '1.25rem', position: 'relative' },
  label: {
    display: 'block',
    fontSize: '0.8rem',
    fontWeight: 600,
    color: '#374151',
    marginBottom: '0.4rem',
  },
  optTag: {
    fontWeight: 400,
    color: '#9ca3af',
    fontSize: '0.75rem',
  },
  textarea: {
    width: '100%',
    padding: '0.65rem 0.75rem',
    border: '1px solid #d1d5db',
    borderRadius: 6,
    fontSize: '0.9rem',
    fontFamily: 'inherit',
    resize: 'vertical',
    outline: 'none',
    boxSizing: 'border-box',
    transition: 'border-color 0.15s',
  },
  charCount: {
    position: 'absolute',
    bottom: '0.4rem',
    right: '0.6rem',
    fontSize: '0.72rem',
    color: '#9ca3af',
    pointerEvents: 'none',
  },

  // ── Private feedback ────────────────────────────────────────
  privateField: {
    background: '#fffbeb',
    border: '1px dashed #fcd34d',
    borderRadius: 8,
    padding: '0.85rem 1rem',
  },
  privateHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.4rem',
    marginBottom: '0.3rem',
  },
  privateIcon:  { fontSize: '1rem' },
  privateLabel: {
    fontWeight: 700,
    fontSize: '0.85rem',
    color: '#92400e',
    margin: 0,
  },
  privateBadge: {
    marginLeft: 'auto',
    fontSize: '0.7rem',
    background: '#fde68a',
    color: '#78350f',
    borderRadius: 99,
    padding: '0.1rem 0.5rem',
    fontWeight: 600,
    whiteSpace: 'nowrap',
  },
  privateHint: {
    fontSize: '0.78rem',
    color: '#92400e',
    margin: '0 0 0.6rem',
    lineHeight: 1.4,
  },
  privateTextarea: {
    background: '#fffdf0',
    borderColor: '#fcd34d',
  },

  // ── Submit ──────────────────────────────────────────────────
  submitBtn: {
    width: '100%',
    padding: '0.75rem',
    background: '#4a6cf7',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    fontSize: '0.95rem',
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'background 0.15s',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  submitBtnDisabled: {
    background: '#a5b4fc',
    cursor: 'not-allowed',
    opacity: 0.7,
  },
  submitInner: { display: 'flex', alignItems: 'center', gap: '0.5rem' },
  spinner: {
    display: 'inline-block',
    width: 16,
    height: 16,
    border: '2px solid rgba(255,255,255,0.3)',
    borderTopColor: '#fff',
    borderRadius: '50%',
    animation: 'tr-spin 0.7s linear infinite',
  },
  validationNote: {
    marginTop: '0.5rem',
    fontSize: '0.78rem',
    color: '#9ca3af',
    textAlign: 'center',
  },

  // ── Error ────────────────────────────────────────────────────
  errorBanner: {
    background: '#fef2f2',
    border: '1px solid #fecaca',
    borderRadius: 6,
    padding: '0.6rem 0.75rem',
    fontSize: '0.875rem',
    color: '#b91c1c',
    marginBottom: '1rem',
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    flexWrap: 'wrap',
  },
  retryBtn: {
    marginLeft: 'auto',
    fontSize: '0.8rem',
    padding: '0.2rem 0.6rem',
    border: '1px solid #fca5a5',
    borderRadius: 4,
    background: 'transparent',
    color: '#b91c1c',
    cursor: 'pointer',
  },

  // ── Locked state ─────────────────────────────────────────────
  lockedWrap: {
    textAlign: 'center',
    padding: '2rem 1rem',
  },
  lockIcon:    { fontSize: '2.5rem', marginBottom: '0.5rem' },
  lockedTitle: { fontSize: '1.1rem', fontWeight: 700, marginBottom: '0.5rem' },
  lockedBody:  { fontSize: '0.875rem', color: '#6b7280', marginBottom: '0.75rem' },
  escrowPill: {
    display: 'inline-block',
    padding: '0.25rem 0.75rem',
    background: '#f3f4f6',
    borderRadius: 99,
    fontSize: '0.8rem',
    color: '#374151',
  },

  // ── Success state ────────────────────────────────────────────
  successWrap: {
    textAlign: 'center',
    padding: '2rem 1rem',
  },
  successIcon: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 52,
    height: 52,
    background: '#d1fae5',
    borderRadius: '50%',
    fontSize: '1.6rem',
    color: '#059669',
    marginBottom: '0.75rem',
  },
  successTitle: { fontSize: '1.1rem', fontWeight: 700, marginBottom: '0.5rem', margin: '0 0 0.5rem' },
  successBody:  { fontSize: '0.875rem', color: '#6b7280', margin: '0 0 1rem' },
}

// Inject the spinner keyframe once (avoids styled-components dependency)
if (typeof document !== 'undefined' && !document.getElementById('tr-keyframes')) {
  const s = document.createElement('style')
  s.id = 'tr-keyframes'
  s.textContent = '@keyframes tr-spin { to { transform: rotate(360deg); } }'
  document.head.appendChild(s)
}
