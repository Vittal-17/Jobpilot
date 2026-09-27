import { useEffect } from 'react';
import Lenis from 'lenis';
import { gsap, ScrollTrigger, REDUCE } from '@/lib/anim';

/**
 * App-wide buttery scrolling (Lenis), synced to GSAP's ticker so ScrollTrigger
 * stays in lockstep. Mounted once in App. Disabled entirely under
 * prefers-reduced-motion — the page then scrolls natively, unchanged.
 */
export function SmoothScroll() {
  useEffect(() => {
    if (REDUCE()) return;

    const lenis = new Lenis({
      duration: 1.05,
      smoothWheel: true,
      touchMultiplier: 1.5,
    });

    lenis.on('scroll', ScrollTrigger.update);
    const ticker = (time: number) => lenis.raf(time * 1000);
    gsap.ticker.add(ticker);
    gsap.ticker.lagSmoothing(0);

    return () => {
      lenis.off('scroll', ScrollTrigger.update);
      gsap.ticker.remove(ticker);
      lenis.destroy();
    };
  }, []);

  return null;
}
