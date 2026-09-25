import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

export function EngineVisual() {
  const svgRef = useRef<SVGSVGElement>(null);

  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      if (!svgRef.current) return;

      const rings = svgRef.current.querySelectorAll('.engine-ring');
      const core = svgRef.current.querySelector('.engine-core');

      gsap.to(rings, {
        rotation: 360,
        transformOrigin: "50% 50%",
        duration: (i) => 20 + i * 10,
        repeat: -1,
        ease: "none",
        stagger: {
          each: 0,
          from: "start",
        }
      });

      // Reverse outer ring
      gsap.to(rings[2], {
        rotation: -360,
        transformOrigin: "50% 50%",
        duration: 40,
        repeat: -1,
        ease: "none",
      });

      gsap.to(core, {
        scale: 1.05,
        opacity: 0.7,
        duration: 2,
        yoyo: true,
        repeat: -1,
        ease: "sine.inOut"
      });
    });
    return () => mm.revert();
  }, { scope: svgRef });

  return (
    <svg
      ref={svgRef}
      viewBox="0 0 200 200"
      className="w-full h-full opacity-40 text-[var(--color-text-primary)]"
      fill="none"
      stroke="currentColor"
      strokeWidth="0.5"
    >
      <circle cx="100" cy="100" r="90" className="engine-ring" strokeDasharray="4 4" />
      <circle cx="100" cy="100" r="60" className="engine-ring" strokeDasharray="1 8" strokeWidth="1.5" />
      <circle cx="100" cy="100" r="30" className="engine-ring" />

      <path className="engine-ring" d="M 100 10 L 100 190 M 10 100 L 190 100" opacity="0.3" />
      <path className="engine-ring" d="M 36 36 L 164 164 M 36 164 L 164 36" opacity="0.3" />

      <circle cx="100" cy="100" r="4" className="engine-core" fill="currentColor" />
    </svg>
  );
}
