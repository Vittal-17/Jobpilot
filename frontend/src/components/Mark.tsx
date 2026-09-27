// Wabi-sabi hand marks — one imperfect, hand-drawn stroke per section.
// The draw-on animation in SectionHead measures each path's real length
// (getTotalLength) and animates the dash from there, so it works regardless
// of the stroke's length — the same technique the signin ring uses.
type MarkVariant = 'underline' | 'swoop' | 'arc' | 'ring' | 'pulse' | 'scribble';

const MARKS: Record<MarkVariant, { viewBox: string; d: string }> = {
  underline: { viewBox: '0 0 300 24',  d: 'M5 15 C 52 6, 108 20, 158 12 C 208 4, 250 19, 295 9' },
  swoop:     { viewBox: '0 0 300 30',  d: 'M6 21 C 72 3, 150 29, 212 15 C 252 7, 276 12, 296 23' },
  arc:       { viewBox: '0 0 300 34',  d: 'M8 13 C 72 41, 228 41, 292 13' },
  ring:      { viewBox: '0 0 320 120', d: 'M20 64 C 30 26, 210 12, 292 34 C 322 42, 312 92, 214 104 C 96 118, 20 100, 14 60' },
  pulse:     { viewBox: '0 0 300 34',  d: 'M4 23 L 86 23 L 116 8 L 146 30 L 176 5 L 202 23 L 296 23' },
  scribble:  { viewBox: '0 0 320 120', d: 'M24 58 C 44 22, 202 16, 288 42 C 320 52, 298 96, 198 106 C 116 113, 30 104, 22 66 C 17 42, 122 30, 252 46' },
};

export function Mark({ variant, className = '' }: { variant: MarkVariant; className?: string }) {
  const m = MARKS[variant];
  return (
    <svg className={`mark mark--${variant} ${className}`} viewBox={m.viewBox} preserveAspectRatio="none" aria-hidden="true">
      <path className="mark-stroke" d={m.d} />
    </svg>
  );
}

export type { MarkVariant };
