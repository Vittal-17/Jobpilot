import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { Link } from 'react-router-dom';
import { useRecommendations } from '@/hooks/useRecommendations';
import { useSystemStatus } from '@/hooks/useSystemStatus';
import { formatDistanceToNow } from 'date-fns';
import { EngineVisual } from '@/components/EngineVisual';

export function Today() {
  const container = useRef<HTMLDivElement>(null);
  const { data: recs, isLoading: recsLoading, isError: recsError } = useRecommendations(1, 10);
  const { data: status, isLoading: statusLoading } = useSystemStatus();

  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.from('.gsap-fade-up', {
        y: 20,
        opacity: 0,
        duration: 0.8,
        stagger: 0.05,
        ease: 'power3.out',
        clearProps: 'all'
      });

      gsap.from('.gsap-mask-reveal', {
        clipPath: 'polygon(0 0, 100% 0, 100% 0, 0 0)',
        opacity: 0,
        y: 10,
        duration: 1,
        stagger: 0.1,
        ease: 'power4.out',
        clearProps: 'all'
      });
    });

    mm.add("(prefers-reduced-motion: reduce)", () => {
      gsap.set('.gsap-fade-up, .gsap-mask-reveal', { opacity: 1, y: 0, clipPath: 'none' });
    });

    return () => mm.revert();
  }, { scope: container, dependencies: [recsLoading, statusLoading] });

  return (
    <div ref={container} className="w-full max-w-7xl mx-auto flex flex-col gap-12">
      {/* Hero Section */}
      <section className="grid grid-cols-1 md:grid-cols-12 gap-8 items-end pb-12 border-b border-[var(--color-border-strong)]">
        <div className="md:col-span-8 flex flex-col gap-4">
          <h1 className="gsap-fade-up text-6xl md:text-8xl font-serif text-[var(--color-text-primary)] leading-[0.9] tracking-tighter">
            Today's <br/> Discoveries.
          </h1>
        </div>
        <div className="gsap-fade-up md:col-span-4 flex justify-end md:justify-center items-center opacity-80 mix-blend-multiply">
          <div className="w-32 h-32 md:w-48 md:h-48">
            <EngineVisual />
          </div>
        </div>
      </section>

      {/* Briefing / Status Box */}
      <section className="gsap-fade-up grid grid-cols-1 md:grid-cols-12 gap-px bg-[var(--color-border)] border border-[var(--color-border-strong)]">
        <div className="bg-[var(--color-bg-primary)] md:col-span-4 p-6 flex flex-col justify-between">
          <h2 className="font-sans text-xs uppercase tracking-widest text-[var(--color-text-secondary)] mb-6">Engine Status</h2>
          <div className="flex flex-col gap-2 font-sans text-sm tracking-wide">
            <div className="flex items-center gap-3">
              <div className={`w-2 h-2 rounded-none ${status?.latest_execution_status === 'failed' ? 'bg-[var(--color-signal-error)]' : status?.engine_active ? 'bg-[var(--color-signal-success)]' : 'bg-[var(--color-signal-error)]'}`}></div>
              <span className="uppercase">
                {status?.latest_execution_status === 'failed' ? 'Execution Failed' : status?.engine_active ? 'Online & Processing' : 'Offline / Standby'}
              </span>
            </div>
            {status?.latest_execution_status && (
              <span className="text-[var(--color-text-secondary)] text-xs uppercase tracking-widest mt-1">
                Last Run: {status.latest_execution_status}
              </span>
            )}
          </div>
        </div>
        <div className="bg-[var(--color-bg-primary)] md:col-span-4 p-6 flex flex-col justify-between">
          <h2 className="font-sans text-xs uppercase tracking-widest text-[var(--color-text-secondary)] mb-6">Telemetry</h2>
          <div className="flex flex-col gap-1 font-serif text-2xl">
            <span>{status?.total_processed !== undefined ? status.total_processed.toLocaleString() : '--'}</span>
            <span className="font-sans text-xs uppercase tracking-widest text-[var(--color-text-secondary)]">Items Processed</span>
          </div>
        </div>
        <div className="bg-[var(--color-bg-primary)] md:col-span-4 p-6 flex flex-col justify-between">
          <h2 className="font-sans text-xs uppercase tracking-widest text-[var(--color-text-secondary)] mb-6">Sync Window</h2>
          <div className="font-sans text-sm uppercase tracking-wide">
            {status?.last_sync ? formatDistanceToNow(new Date(status.last_sync), { addSuffix: true }) : '--'}
          </div>
        </div>
      </section>

      {/* Job Matches Feed */}
      <section className="flex flex-col gap-px bg-[var(--color-border-strong)] border-t border-[var(--color-border-strong)] mt-4">

        {recsLoading && (
          <div className="bg-[var(--color-bg-primary)] p-8 animate-pulse font-sans text-xs uppercase tracking-widest text-[var(--color-text-secondary)]">
            Retrieving matches...
          </div>
        )}

        {recsError && (
          <div className="bg-[var(--color-bg-primary)] p-8 text-[var(--color-signal-error)] font-sans text-sm">
            Error: Engine connection lost. Operating offline.
          </div>
        )}

        {!recsLoading && !recsError && recs?.items.length === 0 && (
          <div className="bg-[var(--color-bg-primary)] p-8 font-serif italic text-xl text-[var(--color-text-secondary)]">
            No matches found today. The engine continues to monitor.
          </div>
        )}

        {!recsLoading && recs?.items?.map(({ job, match, recommended_at, delivery_status }) => (
          <article
            key={job.id}
            className="gsap-mask-reveal bg-[var(--color-bg-primary)] grid grid-cols-1 md:grid-cols-12 gap-6 p-6 hover:bg-[var(--color-bg-secondary)] transition-colors duration-300"
          >
            {/* Left: Metadata */}
            <div className="md:col-span-3 flex flex-col gap-2 font-sans text-xs uppercase tracking-widest text-[var(--color-text-secondary)]">
              <span>{job.source}</span>
              <span>{formatDistanceToNow(new Date(recommended_at), { addSuffix: true })}</span>
              {match?.score !== null && match?.score !== undefined && (
                <div className="mt-2 text-[var(--color-text-primary)] font-medium">
                  Match: {match.score}%
                </div>
              )}
              <div className="flex flex-wrap gap-2 mt-2">
                {job.remote && (
                  <div className="border border-[var(--color-border)] px-2 py-1">
                    Remote
                  </div>
                )}
                {delivery_status === 'DELIVERED' && (
                  <div className="border border-[var(--color-border)] px-2 py-1">
                    Notified
                  </div>
                )}
              </div>
            </div>

            {/* Center: Content */}
            <div className="md:col-span-7 flex flex-col gap-3 border-l border-[var(--color-border)] pl-6">
              <h3 className="font-serif text-3xl md:text-4xl text-[var(--color-text-primary)] tracking-tight leading-none">
                {job.title}
              </h3>
              <p className="font-sans text-sm uppercase tracking-wide text-[var(--color-text-primary)]">
                {job.company} {job.location ? `— ${job.location}` : ''}
              </p>

              {match?.reasons && match.reasons.length > 0 && (
                <ul className="mt-2 flex flex-col gap-1 font-sans text-xs text-[var(--color-text-secondary)]">
                  {match.reasons.slice(0, 3).map((r, i) => (
                    <li key={i} className="flex gap-2">
                      <span className="text-[var(--color-border-strong)] opacity-50">+</span>
                      {r.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* Right: Action */}
            <div className="md:col-span-2 flex items-center md:justify-end border-l border-[var(--color-border)] pl-6">
              <Link
                to={`/jobs/${job.id}`}
                className="group flex items-center gap-2 font-sans text-xs uppercase tracking-widest text-[var(--color-text-primary)] border-b border-transparent hover:border-[var(--color-text-primary)] transition-colors"
              >
                Inspect
                <span className="group-hover:translate-x-1 transition-transform">→</span>
              </Link>
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}
