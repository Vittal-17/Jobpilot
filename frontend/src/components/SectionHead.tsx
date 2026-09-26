import { useRef, type ReactNode } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

interface SectionHeadProps {
  index: string;
  kicker: string;
  title: ReactNode;
  deck?: ReactNode;
  aside?: ReactNode;
}

/**
 * The editorial opener for every signed-in section: a cropped outline
 * numeral, a mono kicker, a large headline (with room for one wabi-sabi
 * hand mark), an optional deck and a right-aligned folio/status.
 */
export function SectionHead({ index, kicker, title, deck, aside }: SectionHeadProps) {
  const ref = useRef<HTMLElement>(null);

  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add('(prefers-reduced-motion: no-preference)', () => {
      gsap.from('.sec-index', { autoAlpha: 0, duration: 0.9, ease: 'power2.out' });
      gsap.from('.sec-kicker, .sec-title, .sec-deck, .sec-aside', {
        y: 16, autoAlpha: 0, duration: 0.6, stagger: 0.08, ease: 'power3.out',
      });
      gsap.fromTo('.mark-stroke',
        { strokeDasharray: 1, strokeDashoffset: 1 },
        { strokeDashoffset: 0, duration: 1.0, delay: 0.45, ease: 'power2.inOut' });
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
