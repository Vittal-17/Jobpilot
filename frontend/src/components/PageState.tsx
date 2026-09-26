import { Link } from 'react-router-dom';
import type { ReactNode } from 'react';

interface PageStateProps {
  tone?: 'neutral' | 'error';
  eyebrow: string;
  title: string;
  body: ReactNode;
  action?: { to: string; label: string };
  /** Optional structural motif rows shown in the right rail (labels only, no data). */
  motif?: { label: string; color?: string }[];
}

/**
 * A deliberate full-viewport composition for loading / empty / error states.
 * Fills the body below a section opener with a strong editorial message on the
 * left and a restrained structural rail on the right — never a single sentence
 * stranded in blank canvas. Warm paper only.
 */
export function PageState({ tone = 'neutral', eyebrow, title, body, action, motif }: PageStateProps) {
  const accent = tone === 'error' ? 'var(--vermillion)' : 'var(--cobalt)';

  return (
    <div style={{ flex: 1, display: 'grid', gridTemplateColumns: 'minmax(0, 1.4fr) minmax(260px, 0.82fr)', minHeight: 0, background: 'var(--cream)' }}>
      {/* Editorial message */}
      <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: 'clamp(32px, 6vh, 76px) clamp(28px, 5vw, 84px)', minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
          <span style={{ width: 30, height: 2, background: accent, flexShrink: 0 }} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, letterSpacing: '0.22em', textTransform: 'uppercase', color: accent }}>
            {eyebrow}
          </span>
        </div>
        <h1 style={{ margin: 0, fontSize: 'clamp(32px, 4.4vw, 58px)', fontWeight: 800, lineHeight: 1.0, letterSpacing: '-0.032em', color: 'var(--ink)', maxWidth: '18ch' }}>
          {title}
        </h1>
        <div style={{ fontSize: 17, color: 'var(--ink-soft)', maxWidth: 520, marginTop: 24, lineHeight: 1.6, borderLeft: `2px solid ${accent}`, paddingLeft: 18 }}>
          {body}
        </div>
        {action && (
          <Link to={action.to} className="ps-action" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginTop: 30, fontSize: 15, fontWeight: 700, color: accent, textDecoration: 'none', fontFamily: 'var(--font-mono)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
            {action.label} →
          </Link>
        )}
      </div>

      {/* Structural rail — occupies space with restrained geometry, not fake data */}
      <div className="hatch" style={{ borderLeft: '1px solid var(--stone)', background: 'var(--sand)', display: 'flex', flexDirection: 'column' }}>
        {(motif ?? [
          { label: 'DISCOVER', color: 'var(--cobalt)' },
          { label: 'NORMALIZE', color: 'var(--violet)' },
          { label: 'ENRICH', color: 'var(--mint)' },
          { label: 'MATCH', color: 'var(--amber)' },
          { label: 'SURFACE', color: 'var(--vermillion)' },
        ]).map((m, i, arr) => (
          <div key={m.label} style={{
            flex: 1, display: 'flex', alignItems: 'center', gap: 13,
            padding: '0 28px', borderBottom: i < arr.length - 1 ? '1px solid color-mix(in srgb, var(--stone) 70%, transparent)' : 'none',
          }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, color: 'var(--stone-dark)', width: 22, flexShrink: 0 }}>
              {String(i + 1).padStart(2, '0')}
            </span>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: m.color ?? 'var(--stone-dark)', flexShrink: 0 }} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, letterSpacing: '0.12em', color: m.color ?? 'var(--ink-muted)' }}>{m.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
