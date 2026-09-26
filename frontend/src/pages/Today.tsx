import { useRef } from 'react';
import { Link } from 'react-router-dom';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { formatDistanceToNow, parseISO } from 'date-fns';
import { useRecommendations } from '@/hooks/useRecommendations';
import { useSystemStatus } from '@/hooks/useSystemStatus';
import { useMemo, useState } from 'react';
import { IntelligenceField } from '@/components/IntelligenceField';

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

export function Today() {
  const [hoveredJobId, setHoveredJobId] = useState<number | null>(null);
  const container = useRef<HTMLDivElement>(null);
  const { data: recs, isLoading: recsLoading, isError: isFeedFailed } = useRecommendations(1, 20);
  const { data: status, isLoading: statusLoading, isError: statusError } = useSystemStatus();

  const engineStatus   = status?.latest_execution_status ?? null;
  const isEngineFailed = engineStatus === 'failed' || statusError;
  const isFailed       = isEngineFailed || isFeedFailed;
  const isOnline       = !!status?.engine_active && !isEngineFailed;
  const isActive       = isOnline;
  const isBusy         = engineStatus === 'started';
  const totalProcessed = status?.total_processed ?? 0;
  const totalMatched   = recs?.total ?? 0;
  const items          = useMemo(() => recs?.items ?? [], [recs?.items]);
  const totalSurfaced  = items.length;

  const availabilityLabel = isOnline ? 'Online' : 'Unreachable';
  const availabilityColor = isOnline ? 'var(--mint)' : 'var(--vermillion)';

  const lead = useMemo(() => {
    if (!items.length) return undefined;
    return items.reduce((best, current) => {
      const bestScore = best.match?.score ?? -1;
      const currScore = current.match?.score ?? -1;
      return currScore > bestScore ? current : best;
    }, items[0]);
  }, [items]);

  const rest = useMemo(() => {
    if (!lead) return [];
    return items.filter(item => item !== lead);
  }, [items, lead]);

  const hasLead = !!lead;

  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add('(prefers-reduced-motion: no-preference)', () => {
      const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
      tl.from('.stage-node', { opacity: 0, x: -14, duration: 0.5, stagger: 0.05, clearProps: 'all' })
        .from('.lead-in',   { opacity: 0, y: 22, duration: 0.6, clearProps: 'all' }, '-=0.2')
        .from('.stream-row', { opacity: 0, x: 20, duration: 0.45, stagger: 0.06, clearProps: 'all' }, '-=0.3');
    });
    return () => mm.revert();
  }, { scope: container, dependencies: [recsLoading, statusLoading] });

  return (
    <div ref={container} style={{
      position: 'relative', minHeight: 'calc(100vh - 52px)',
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
      background: 'linear-gradient(108deg, var(--cream) 0%, var(--cream) 42%, var(--sand) 100%)',
    }}>
      {/* The funnel substrate — the pipeline itself, behind everything. */}
      <IntelligenceField isActive={isActive} isFailed={isFailed} isLoading={recsLoading || statusLoading} items={items} hoveredJobId={hoveredJobId} />

      {/* Quiet system line — demoted to the top edge, never a KPI header. */}
      <div style={{
        position: 'relative', zIndex: 3,
        display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 10,
        padding: '16px 40px 0', flexShrink: 0,
      }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%', background: availabilityColor, flexShrink: 0,
          boxShadow: isBusy ? `0 0 0 4px color-mix(in srgb, ${availabilityColor} 22%, transparent)` : 'none',
        }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)' }}>{availabilityLabel}</span>
        {engineStatus && !isEngineFailed && (
          <span style={{ fontSize: 13, color: 'var(--ink-muted)' }}>
            · run {engineStatus} {status?.last_sync ? formatDistanceToNow(parseISO(status.last_sync)) + ' ago' : ''}
          </span>
        )}
      </div>

      {/* One continuous field: funnel rail bleeds into the lead, which bleeds
          into the surfaced stream. No cards, no seams, no slabs. */}
      <div style={{
        position: 'relative', zIndex: 2, flex: 1,
        display: 'grid', gridTemplateColumns: 'minmax(0, 1.5fr) minmax(300px, 0.72fr)',
        alignItems: 'stretch',
      }}>
        {/* LEAD territory (with the funnel stage-rail overlapping its left) */}
        <div style={{ position: 'relative', display: 'flex', minWidth: 0 }}>
          {/* Stage rail — DISCOVER↓SURFACE traces the funnel narrowing inward.
              Vertical flow, not a horizontal metric strip. */}
          <div className="stage-rail" style={{
            display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 2,
            padding: '0 22px 0 40px', flexShrink: 0,
          }}>
            {STAGES.map((s, i) => {
              const count = i === 0 ? totalProcessed : i === 5 ? totalSurfaced : null;
              const indent = i * 6; // subtle inward stagger = convergence
              return (
                <div key={s.id} className="stage-node" style={{
                  display: 'flex', alignItems: 'baseline', gap: 8,
                  marginLeft: indent, padding: '5px 0',
                }}>
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: s.color, flexShrink: 0, alignSelf: 'center' }} />
                  <span style={{ fontSize: 12, fontWeight: 800, letterSpacing: '0.1em', color: s.color, minWidth: 74 }}>{s.id}</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, color: 'var(--ink)' }}>
                    {statusLoading ? '·' : count !== null ? count.toLocaleString() : '—'}
                  </span>
                </div>
              );
            })}
          </div>

          {/* The lead match — a unified object. */}
          <div className="lead-in" style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '32px 32px 48px 8px' }}>
            <div style={{ fontSize: 13, fontWeight: 800, letterSpacing: '0.14em', textTransform: 'uppercase', color: isFailed ? 'var(--vermillion)' : 'var(--ink-muted)', marginBottom: 20 }}>
              {isEngineFailed ? 'Intelligence engine unreachable' : isFeedFailed ? 'Recommendations stream unavailable'
                : hasLead ? `Strongest signal · ${formatDistanceToNow(parseISO(lead.recommended_at))} ago`
                : isActive ? 'Listening · vectors active' : 'Standing by'}
            </div>

            {isEngineFailed || isFeedFailed ? (
              <div style={{ maxWidth: 620 }}>
                <div style={{ fontSize: 'clamp(36px, 4.6vw, 64px)', fontWeight: 800, lineHeight: 1.0, letterSpacing: '-0.03em', color: 'var(--ink)' }}>
                  {isEngineFailed ? 'The stream went quiet.' : 'Failed to retrieve recommendations.'}
                </div>
                <p style={{ fontSize: 17, color: 'var(--ink-soft)', maxWidth: 500, marginTop: 22 }}>
                  {isEngineFailed ? `${totalProcessed.toLocaleString()} items were processed before contact dropped. Recommendations resurface the moment the engine responds.` : 'The backend engine is active, but the personalized recommendation feed could not be fetched.'}
                </p>
              </div>
            ) : hasLead ? (
              <Link to={`/jobs/${lead.job.id}`} onMouseEnter={() => setHoveredJobId(lead.job.id)} onMouseLeave={() => setHoveredJobId(null)} style={{ textDecoration: 'none', color: 'inherit', display: 'block', maxWidth: 720 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 'clamp(14px, 2vw, 28px)', flexWrap: 'wrap' }}>
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: 'clamp(72px, 9vw, 140px)', fontWeight: 800,
                    lineHeight: 0.82, letterSpacing: '-0.05em', color: scoreColor(lead.match?.score),
                  }}>
                    {lead.match?.score ?? '—'}
                  </span>
                  <div style={{ minWidth: 220 }}>
                    <div style={{ fontSize: 'clamp(30px, 3.6vw, 56px)', fontWeight: 800, lineHeight: 1.04, letterSpacing: '-0.03em', color: 'var(--ink)' }}>
                      {lead.job.title}
                    </div>
                    <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'baseline', marginTop: 10 }}>
                      <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--cobalt)' }}>{lead.job.company}</span>
                      {lead.job.location && <span style={{ fontSize: 15, color: 'var(--ink-muted)' }}>{lead.job.location}</span>}
                      {lead.job.remote && <span style={{ fontSize: 12, fontWeight: 800, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--acid)' }}>Remote</span>}
                      {lead.job.employment_type && <span style={{ fontSize: 15, color: 'var(--ink-muted)' }}>{lead.job.employment_type}</span>}
                    </div>
                  </div>
                </div>
                {lead.match?.reasons && lead.match.reasons.length > 0 && (
                  <div style={{ marginTop: 24, display: 'flex', flexDirection: 'column', gap: 9, maxWidth: 560, borderLeft: `2px solid ${scoreColor(lead.match.score)}`, paddingLeft: 18 }}>
                    {lead.match.reasons.slice(0, 3).map((r, i) => (
                      <span key={i} style={{ fontSize: 16, color: 'var(--ink-soft)', lineHeight: 1.45 }}>{r.message}</span>
                    ))}
                  </div>
                )}
              </Link>
            ) : (
              <div style={{ maxWidth: 640 }}>
                <div style={{ fontSize: 'clamp(36px, 4.6vw, 64px)', fontWeight: 800, lineHeight: 1.0, letterSpacing: '-0.03em', color: 'var(--ink)' }}>
                  {totalProcessed > 0 ? `${totalProcessed.toLocaleString()} signals in flight.` : 'The pipeline is warming up.'}
                </div>
                <p style={{ fontSize: 17, color: 'var(--ink-soft)', maxWidth: 500, marginTop: 22 }}>
                  Nothing has cleared the match threshold yet. Discovery, normalization and enrichment keep running — the strongest role surfaces here the instant it qualifies.
                </p>
                <Link to="/search" style={{ display: 'inline-block', marginTop: 26, fontSize: 16, fontWeight: 700, color: 'var(--cobalt)', textDecoration: 'none' }}>
                  Tune the search vectors →
                </Link>
              </div>
            )}
          </div>
        </div>

        {/* SURFACED STREAM — a warm wash bleeding in from the right edge,
            not a dark sidebar slab. */}
        <div style={{
          position: 'relative', alignSelf: 'stretch',
          display: 'flex', flexDirection: 'column', minWidth: 0,
          background: 'linear-gradient(90deg, transparent 0%, color-mix(in srgb, var(--sand) 55%, transparent) 22%, var(--sand) 100%)',
          borderLeft: '1px solid color-mix(in srgb, var(--stone) 60%, transparent)',
        }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', padding: '28px 32px 16px' }}>
            <span style={{ fontSize: 13, fontWeight: 800, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--ink-muted)' }}>
              Surfaced stream
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700, color: 'var(--ink-soft)' }}>
              {totalSurfaced}/{totalMatched.toLocaleString()}
            </span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflowY: 'auto' }}>
            {rest.map(({ job, match, recommended_at }) => (
              <Link key={job.id} to={`/jobs/${job.id}`} className="stream-row" style={{
                display: 'grid', gridTemplateColumns: '52px 1fr', gap: 14, alignItems: 'baseline',
                padding: '16px 32px', textDecoration: 'none', color: 'inherit',
                borderTop: '1px solid color-mix(in srgb, var(--stone) 55%, transparent)', transition: 'background 0.14s',
              }}
                onMouseEnter={e => { e.currentTarget.style.background = 'color-mix(in srgb, var(--cream) 55%, transparent)'; setHoveredJobId(job.id); }}
                onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; setHoveredJobId(null); }}
              >
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 800, lineHeight: 1, letterSpacing: '-0.04em', color: scoreColor(match?.score) }}>
                  {match?.score ?? '—'}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 17, fontWeight: 600, color: 'var(--ink)', lineHeight: 1.25 }}>{job.title}</div>
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'baseline', marginTop: 4 }}>
                    <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--cobalt)' }}>{job.company}</span>
                    <span style={{ fontSize: 13, color: 'var(--ink-muted)' }}>{formatDistanceToNow(parseISO(recommended_at))} ago</span>
                  </div>
                  {match?.reasons?.[0] && (
                    <div style={{ fontSize: 14, color: 'var(--ink-soft)', marginTop: 6, lineHeight: 1.4 }}>{match.reasons[0].message}</div>
                  )}
                </div>
              </Link>
            ))}

            {!recsLoading && rest.length === 0 && (
              <div style={{ padding: '24px 32px', fontSize: 15, color: 'var(--ink-muted)', lineHeight: 1.5 }}>
                {hasLead ? 'This is the only role above threshold right now.' : isEngineFailed ? 'Stream suspended until the engine reconnects.' : isFeedFailed ? 'Feed unreachable.' : 'The stream fills as roles clear the match threshold.'}
              </div>
            )}
          </div>

          <div style={{ padding: '22px 32px', borderTop: '1px solid color-mix(in srgb, var(--stone) 60%, transparent)', display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Link to="/jobs" style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)', textDecoration: 'none' }}>
              Full archive{totalMatched > totalSurfaced ? ` · +${(totalMatched - totalSurfaced).toLocaleString()}` : ''} →
            </Link>
            <Link to="/search" style={{ fontSize: 15, fontWeight: 600, color: 'var(--ink-muted)', textDecoration: 'none' }}>
              Configure search vectors →
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
