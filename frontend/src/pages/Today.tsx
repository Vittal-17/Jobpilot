import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { useRecommendations } from '@/hooks/useRecommendations';
import { useSystemStatus } from '@/hooks/useSystemStatus';
import { formatDistanceToNow } from 'date-fns';

export function Today() {
  const container = useRef<HTMLDivElement>(null);
  const { data: recs, isLoading, isError } = useRecommendations(1, 10);
  const { data: status } = useSystemStatus();

  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.from('.gsap-fade-up', {
        y: 20,
        opacity: 0,
        duration: 0.8,
        stagger: 0.1,
        ease: 'power3.out',
        clearProps: 'all'
      });
    });
    mm.add("(prefers-reduced-motion: reduce)", () => {
      gsap.set('.gsap-fade-up', { opacity: 1, y: 0 });
    });
    return () => mm.revert();
  }, { scope: container });

  return (
    <div ref={container} className="max-w-5xl mx-auto">
      <header className="mb-16 md:mb-24 mt-8">
        <h1 className="gsap-fade-up text-5xl md:text-7xl font-serif text-[var(--color-text-primary)] leading-tight tracking-tight mb-4">
          Today's <br className="hidden md:block"/> Discoveries.
        </h1>
        <div className="gsap-fade-up flex items-center gap-3 text-[var(--color-text-secondary)] font-sans text-sm">
          <div className={`w-2 h-2 rounded-full ${status?.engine_active ? 'bg-[var(--color-signal-success)]' : 'bg-[var(--color-signal-error)]'}`}></div>
          <p>
            {status?.engine_active ? 'Engine active.' : 'Engine offline.'}
            {status?.last_sync && ` Last sync ${formatDistanceToNow(new Date(status.last_sync), { addSuffix: true })}.`}
          </p>
        </div>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-12 gap-12">
        <div className="md:col-span-4">
          <div className="gsap-fade-up sticky top-12 p-6 border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-sm">
            <h2 className="font-serif text-xl mb-4">Briefing</h2>
            <p className="text-[var(--color-text-secondary)] mb-4">
              The automation engine has evaluated incoming opportunities against your taxonomy.
              Here are the most significant matches requiring your attention.
            </p>
            <div className="pt-4 border-t border-[var(--color-border)] flex justify-between">
              <span>Processed</span>
              <span className="font-medium text-[var(--color-text-primary)]">{status?.total_processed || '--'} items</span>
            </div>
            <div className="pt-2 flex justify-between">
              <span>Matched</span>
              <span className="font-medium text-[var(--color-text-primary)]">{recs?.total || '--'} items</span>
            </div>
          </div>
        </div>

        <div className="md:col-span-8 flex flex-col gap-6">
          {isLoading && (
            <div className="gsap-fade-up animate-pulse space-y-4">
              <div className="h-6 bg-[var(--color-border)] w-1/3"></div>
              <div className="h-4 bg-[var(--color-border)] w-2/3"></div>
            </div>
          )}

          {isError && (
            <div className="gsap-fade-up p-4 border border-[var(--color-signal-error)] text-[var(--color-signal-error)] bg-[var(--color-signal-error)]/10 text-sm">
              Error: Engine connection lost. Operating offline.
            </div>
          )}

          {!isLoading && !isError && recs?.items.length === 0 && (
            <div className="gsap-fade-up text-lg font-serif italic text-[var(--color-text-secondary)]">
              No matches found today. The engine continues to monitor.
            </div>
          )}

          {!isLoading && recs?.items?.map(({ job, match, recommended_at, delivery_status }) => (
            <article
              key={job.id}
              className="gsap-fade-up group relative flex flex-col p-6 md:p-8 border border-[var(--color-border)] hover:border-[var(--color-text-primary)] transition-colors duration-300 bg-white/40"
            >
              <div className="flex justify-between items-start mb-4">
                <span className="text-xs uppercase tracking-widest text-[var(--color-text-secondary)]">
                  {job.source}
                </span>
                <span className="text-xs text-[var(--color-text-secondary)]">
                  {formatDistanceToNow(new Date(recommended_at), { addSuffix: true })}
                </span>
              </div>
              <h3 className="font-serif text-2xl md:text-3xl mb-2">{job.title}</h3>
              <p className="font-sans text-sm font-medium mb-4">{job.company} {job.location ? `— ${job.location}` : ''}</p>

              <div className="mb-6 p-4 border border-[var(--color-border)] bg-[var(--color-bg-primary)]">
                {match ? (
                  <>
                    <div className="flex justify-between items-center mb-2 text-sm">
                      <span className="font-serif">Match Score</span>
                      <span className="font-medium">{match.score !== null ? `${match.score}%` : 'N/A'}</span>
                    </div>
                    <ul className="text-xs text-[var(--color-text-secondary)] space-y-1">
                      {match.reasons?.map((r, i) => <li key={i}>• {r.message}</li>)}
                    </ul>
                  </>
                ) : (
                  <div className="text-xs text-[var(--color-text-secondary)] italic">
                    Historical match details unavailable.
                  </div>
                )}
              </div>

              <div className="mt-auto flex justify-between items-center pt-6 border-t border-[var(--color-border)] text-sm">
                <div className="flex gap-4">
                  {job.remote && <span className="px-2 py-1 bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-xs">Remote</span>}
                  {delivery_status === 'DELIVERED' && <span className="px-2 py-1 bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-xs">Notified</span>}
                </div>
                <button className="text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors">
                  View Detail →
                </button>
              </div>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}
