import { useRef, useMemo } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

interface IntelligenceFieldProps {
  isActive: boolean;
  isFailed?: boolean;
  isLoading?: boolean;
  items?: any[];
  hoveredJobId?: number | null;
}

const STAGES = [
  { id: 'DISCOVER',  color: 'var(--cobalt)' },
  { id: 'NORMALIZE', color: 'var(--violet)' },
  { id: 'DEDUPE',    color: 'var(--electric)' },
  { id: 'ENRICH',    color: 'var(--mint)' },
  { id: 'MATCH',     color: 'var(--amber)' },
  { id: 'SURFACE',   color: 'var(--vermillion)' },
];

function scoreColor(score: number | null | undefined): string {
  if (score == null) return 'var(--ink-muted)';
  if (score >= 80)   return 'var(--cobalt)';
  if (score >= 60)   return 'var(--amber)';
  return 'var(--ink-soft)';
}

// Deterministic PRNG for stable rendering
const prng = (seed: number) => {
  let t = seed + 0x6D2B79F5;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

export function IntelligenceField({ isActive, isFailed = false, isLoading = false, items = [], hoveredJobId = null }: IntelligenceFieldProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  const maxScore = useMemo(() => {
    return items.reduce((max, item) => Math.max(max, item.match?.score ?? 0), 0);
  }, [items]);

  const nodes = useMemo(() => {
    if (!items || items.length === 0) return [];

    return items.map((item) => {
      const score = item.match?.score ?? 50;
      const clampedScore = Math.max(50, Math.min(100, score));
      // Map score 50-100 to Y-coordinates 900-100 (top is 100)
      const targetY = 900 - ((clampedScore - 50) / 50) * 800;

      const rand = prng(item.job.id);
      // Disperse the entry points on the left
      const startY = 500 + (rand * 800 - 400);

      const isLead = score === maxScore && score > 0;

      return {
        id: item.job.id,
        score,
        isLead,
        targetY,
        pathData: `M -50 ${startY} C 300 ${startY}, 600 ${targetY}, 1050 ${targetY}`,
        color: scoreColor(score),
        rand
      };
    }).sort((a, b) => a.score - b.score);
  }, [items, maxScore]);

  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add('(prefers-reduced-motion: no-preference)', () => {
      if (nodes.length > 0) {
        gsap.fromTo('.if-data-path',
          { strokeDasharray: '0 2000' },
          { strokeDasharray: '2000 0', duration: 1.5, ease: 'power3.out', stagger: 0.1, clearProps: 'strokeDasharray' }
        );
        gsap.fromTo('.if-data-node',
          { scale: 0, autoAlpha: 0, transformOrigin: 'center' },
          { scale: 1, autoAlpha: 1, duration: 0.8, ease: 'back.out(1.5)', stagger: 0.1, delay: 0.5, clearProps: 'all' }
        );
      } else if (isActive && !isLoading) {
        gsap.to('.if-scan-path', {
          x: 100,
          duration: 4,
          ease: 'none',
          repeat: -1,
          yoyo: true,
          stagger: { each: 0.2, from: 'random' }
        });
      }
    });
    return () => mm.revert();
  }, { scope: containerRef, dependencies: [nodes.length, isActive, isLoading] });

  return (
    <div ref={containerRef} aria-hidden style={{ position: 'absolute', inset: 0, overflow: 'hidden', pointerEvents: 'none' }}>
      <svg width="100%" height="100%" viewBox="0 0 1000 1000" preserveAspectRatio="none" style={{ display: 'block', overflow: 'hidden' }}>
        <defs>
          <linearGradient id="if-zones" x1="0" y1="0" x2="1" y2="0">
            {STAGES.map((s, i) => (
              <stop key={s.id} offset={`${(i / 5) * 100}%`} stopColor={s.color} stopOpacity={isFailed ? 0.08 : 0.13} />
            ))}
          </linearGradient>
        </defs>

        <rect x="0" y="0" width="1000" height="1000" fill="url(#if-zones)" opacity={isActive ? 1 : 0.6} />

        {STAGES.map((s, i) => {
          const x = (i / 6) * 1000;
          return <line key={s.id} x1={x} y1="0" x2={x} y2="1000" stroke={s.color} strokeWidth="1" strokeOpacity="0.12" strokeDasharray="3 7" />;
        })}

        {nodes.length > 0 ? nodes.map(node => {
          const isHovered = hoveredJobId === node.id;
          const isDimmed = hoveredJobId !== null && !isHovered;
          const opacity = isDimmed ? 0.15 : (node.isLead ? 1 : 0.65);
          const nodeRadius = isHovered ? 8 : (node.isLead ? 6 : 4);

          return (
            <g key={node.id} style={{ transition: 'opacity 0.4s ease', opacity }}>
              <path
                className="if-data-path"
                d={node.pathData}
                fill="none"
                stroke={node.color}
                strokeWidth={node.isLead ? 3 : (isHovered ? 3 : 1.5)}
                style={{ transition: 'stroke-width 0.3s ease' }}
              />
              <circle
                className="if-data-node"
                cx="980"
                cy={node.targetY}
                r={nodeRadius}
                fill={node.color}
                style={{ transition: 'r 0.3s ease, fill 0.3s ease' }}
              />
              {node.isLead && !isDimmed && (
                <circle
                  cx="980"
                  cy={node.targetY}
                  r={14}
                  fill="none"
                  stroke={node.color}
                  strokeOpacity={0.4}
                  strokeWidth={1}
                />
              )}
            </g>
          );
        }) : (
          Array.from({ length: 8 }).map((_, i) => (
            <path
              key={`scan-${i}`}
              className="if-scan-path"
              d={`M -150 ${200 + i * 85} Q 400 ${200 + i * 85 + (i % 2 === 0 ? 60 : -60)} 1150 ${200 + i * 85}`}
              stroke="var(--cobalt)"
              strokeOpacity={0.15}
              strokeWidth={1.5}
              fill="none"
              strokeDasharray="4 12"
            />
          ))
        )}
      </svg>
    </div>
  );
}
