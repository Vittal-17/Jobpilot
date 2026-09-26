import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

interface PipelineProps {
  totalProcessed: number;
  totalMatched?: number | null;
  totalSurfaced?: number | null;
  engineStatus: string | null;
  isActive: boolean;
}

const STAGES = [
  { id: 'DISCOVER',  color: 'var(--cobalt)',     sub: 'Raw signals ingested' },
  { id: 'NORMALIZE', color: 'var(--violet)',     sub: 'Schema standardised' },
  { id: 'DEDUPE',    color: 'var(--electric)',   sub: 'Duplicates removed' },
  { id: 'ENRICH',    color: 'var(--mint)',       sub: 'Metadata enriched' },
  { id: 'MATCH',     color: 'var(--amber)',      sub: 'Profile scored' },
  { id: 'SURFACE',   color: 'var(--vermillion)', sub: 'Ready for review' },
];

function getCount(stage: string, totalProcessed: number, totalMatched?: number | null, totalSurfaced?: number | null): number | string {
  if (!totalProcessed && totalProcessed !== 0) return '—';
  switch (stage) {
    case 'DISCOVER':  return totalProcessed;
    case 'NORMALIZE': return '—';
    case 'DEDUPE':    return '—';
    case 'ENRICH':    return '—';
    case 'MATCH':     return totalMatched ?? '—';
    case 'SURFACE':   return totalSurfaced ?? '—';
    default:          return '—';
  }
}

export function Pipeline({ totalProcessed, totalMatched, totalSurfaced, engineStatus, isActive }: PipelineProps) {
  const ref = useRef<HTMLDivElement>(null);

  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add('(prefers-reduced-motion: no-preference)', () => {
      const counters = ref.current?.querySelectorAll('.pipe-count');
      counters?.forEach(el => {
        const targetAttr = el.getAttribute('data-target');
        if (targetAttr === '—') return;

        const target = parseInt(targetAttr || '0', 10);
        if (isNaN(target)) return;

        gsap.fromTo(el,
          { textContent: '0' },
          {
            textContent: target,
            duration: 1.2,
            ease: 'power2.out',
            snap: { textContent: 1 },
            delay: 0.2,
            onUpdate() {
              const v = Math.round(parseFloat(el.textContent || '0'));
              el.textContent = v.toLocaleString();
            },
          }
        );
      });
    });
    return () => mm.revert();
  }, { scope: ref, dependencies: [totalProcessed, totalMatched, totalSurfaced] });

  const active = isActive && engineStatus !== 'failed';

  return (
    <div ref={ref} style={{ display: 'flex', flexShrink: 0, flexWrap: 'wrap', borderTop: '1px solid var(--stone)', borderBottom: '1px solid var(--stone)', background: 'var(--cream)' }}>
      {STAGES.map((s, i) => {
        const count = getCount(s.id, totalProcessed, totalMatched, totalSurfaced);
        const dot = active ? s.color : 'var(--stone-dark)';
        const hasValue = typeof count === 'number';
        return (
          <div
            key={s.id}
            style={{
              flex: '1 1 160px', minWidth: 0, position: 'relative',
              padding: '22px 22px 20px',
              borderRight: i < STAGES.length - 1 ? '1px solid var(--stone)' : 'none',
            }}
          >
            {/* Stage label + step index */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 14 }}>
              <span style={{ width: 9, height: 9, borderRadius: '50%', background: dot, flexShrink: 0, transition: 'background 0.4s ease' }} />
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--ink-muted)' }}>
                {s.id}
              </span>
              <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--stone-dark)' }}>
                {String(i + 1).padStart(2, '0')}
              </span>
            </div>

            {/* Big count */}
            <div
              className="pipe-count"
              data-target={count}
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 'clamp(30px, 3.4vw, 52px)',
                fontWeight: 700,
                lineHeight: 1,
                color: hasValue ? 'var(--ink)' : 'var(--stone-dark)',
                letterSpacing: '-0.04em',
              }}
            >
              {hasValue ? count.toLocaleString() : count}
            </div>

            {/* Sub label */}
            <div style={{ fontSize: 13, color: 'var(--ink-muted)', marginTop: 10, lineHeight: 1.35 }}>
              {s.sub}
            </div>
          </div>
        );
      })}
    </div>
  );
}
