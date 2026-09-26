import { useRef, type ReactNode } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

/**
 * Per-section heading entrance. Each signed-in page picks a different one so
 * navigating between sections feels distinct. Every variant animates *from* a
 * transformed state back to the element's natural resting position, so the
 * design at rest is byte-for-byte unchanged.
 */
export type Reveal = 'rise' | 'clip' | 'blur' | 'skew' | 'slide' | 'scale';

interface SectionHeadProps {
  index: string;
  kicker: string;
  title: ReactNode;
  deck?: ReactNode;
  aside?: ReactNode;
  reveal?: Reveal;
}

/**
 * The editorial opener for every signed-in section: a cropped outline
 * numeral, a mono kicker, a large headline (with room for one wabi-sabi
 * hand mark), an optional deck and a right-aligned folio/status.
 */
export function SectionHead({ index, kicker, title, deck, aside, reveal = 'rise' }: SectionHeadProps) {
  const ref = useRef<HTMLElement>(null);

  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add('(prefers-reduced-motion: no-preference)', () => {
      // The cropped folio numeral drifts up on a slow, generous ease everywhere.
      gsap.from('.sec-index', { autoAlpha: 0, yPercent: 8, duration: 1.0, ease: 'power2.out' });

      const supporting = '.sec-kicker, .sec-deck, .sec-aside';
      const title = '.sec-title';

      if (reveal === 'clip') {
        // The Stream: headline wipes in left→right behind a moving edge.
        gsap.from(title, { clipPath: 'inset(0 100% 0 0)', duration: 0.95, ease: 'power4.out' });
        gsap.from(supporting, { y: 14, autoAlpha: 0, duration: 0.6, stagger: 0.08, delay: 0.12, ease: 'power3.out' });
      } else if (reveal === 'blur') {
        // Search: headline resolves out of a defocus, as if tuning in.
        gsap.from(title, { filter: 'blur(16px)', autoAlpha: 0, y: 18, duration: 0.9, ease: 'power2.out' });
        gsap.from(supporting, { autoAlpha: 0, y: 12, duration: 0.6, stagger: 0.08, delay: 0.15, ease: 'power3.out' });
      } else if (reveal === 'skew') {
        // Saved: headline shears up into place off its left edge.
        gsap.from(title, { skewY: 6, y: 34, autoAlpha: 0, transformOrigin: 'left top', duration: 0.8, ease: 'power4.out' });
        gsap.from(supporting, { y: 16, autoAlpha: 0, duration: 0.55, stagger: 0.08, delay: 0.1, ease: 'power3.out' });
      } else if (reveal === 'slide') {
        // Applied: lead enters from the left, the folio/status from the right.
        gsap.from('.sec-lead', { x: -44, autoAlpha: 0, duration: 0.75, ease: 'power3.out' });
        gsap.from('.sec-aside', { x: 44, autoAlpha: 0, duration: 0.75, ease: 'power3.out' });
      } else if (reveal === 'scale') {
        // System: headline settles down from a slight overscale, transmission-lock feel.
        gsap.from(title, { scale: 1.14, autoAlpha: 0, transformOrigin: 'left center', duration: 0.85, ease: 'power3.out' });
        gsap.from(supporting, { y: 14, autoAlpha: 0, duration: 0.6, stagger: 0.08, delay: 0.12, ease: 'power3.out' });
      } else {
        // rise — the classic vertical stagger (Settings + default).
        gsap.from('.sec-kicker, .sec-title, .sec-deck, .sec-aside', {
          y: 16, autoAlpha: 0, duration: 0.6, stagger: 0.08, ease: 'power3.out',
        });
      }

      // Draw each hand mark on from its own real geometric length — the same
      // technique the signin ring uses. Measuring the path (rather than relying
      // on pathLength normalisation, which browsers scale inconsistently) makes
      // the dash a real unit, so the stroke reliably hides then draws in.
      ref.current?.querySelectorAll<SVGPathElement>('.mark-stroke').forEach((path) => {
        const len = path.getTotalLength() || 300;
        gsap.fromTo(path,
          { strokeDasharray: len, strokeDashoffset: len },
          { strokeDashoffset: 0, duration: 1.0, delay: 0.45, ease: 'power2.inOut' });
      });
    });
    return () => mm.revert();
  }, { scope: ref });

  return (
    <header className="sec" ref={ref}>
      <span className="sec-index" aria-hidden="true">{index}</span>
      <div className="sec-row">
        <div className="sec-lead">
          <div className="sec-kicker">{kicker}</div>
          <h1 className="sec-title">{title}</h1>
          {deck && <p className="sec-deck">{deck}</p>}
        </div>
        {aside && <div className="sec-aside">{aside}</div>}
      </div>
    </header>
  );
}
