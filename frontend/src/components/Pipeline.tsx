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
  { id: 'DISCOVER',  bg: '#1B3FCC', sub: 'Raw signals ingested' },
  { id: 'NORMALIZE', bg: '#4A14A0', sub: 'Schema standardised' },
  { id: 'DEDUPE',    bg: '#005BA8', sub: 'Duplicates removed' },
  { id: 'ENRICH',    bg: '#007050', sub: 'Metadata enriched' },
  { id: 'MATCH',     bg: '#7A5800', sub: 'Profile scored' },
  { id: 'SURFACE',   bg: '#B82C1C', sub: 'Ready for review' },
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

  return (
    <div ref={ref} style={{ display: 'flex', flexShrink: 0 }}>
      {STAGES.map((s, i) => {
        const count = getCount(s.id, totalProcessed, totalMatched, totalSurfaced);
        const active = isActive && engineStatus !== 'failed';
        const bg = active ? s.bg : '#5A5550';
        return (
          <div
            key={s.id}
            style={{
              flex: 1,
              background: bg,
              padding: '20px 16px 18px',
              position: 'relative',
              borderRight: i < 5 ? '2px solid var(--cream)' : 'none',
              transition: 'background 0.4s ease',
              minWidth: 0,
            }}
          >
            {/* Stage label */}
            <div style={{
              fontSize: 12, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase',
              color: 'rgba(255,255,255,0.65)', marginBottom: 6,
            }}>
              {s.id}
            </div>

            {/* Big count */}
            <div
              className="pipe-count"
              data-target={count}
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 'clamp(28px, 3.5vw, 56px)',
                fontWeight: 700,
                lineHeight: 1,
                color: '#fff',
                letterSpacing: '-0.03em',
              }}
            >
              {typeof count === 'number' ? count.toLocaleString() : count}
            </div>

            {/* Sub label */}
            <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.55)', marginTop: 6, lineHeight: 1.3 }}>
              {s.sub}
            </div>

            {/* Arrow connector */}
            {i < 5 && (
              <div style={{
                position: 'absolute', right: -14, top: '50%', transform: 'translateY(-50%)',
                width: 28, height: 28, background: 'var(--cream)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 14, fontWeight: 700, color: active ? s.bg : '#5A5550',
                zIndex: 10, borderRadius: '50%',
                flexShrink: 0,
                transition: 'color 0.4s ease',
              }}>
                →
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
