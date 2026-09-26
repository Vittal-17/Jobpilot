import { useRef } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { useAuth } from '@/hooks/useAuth';
import { useJob } from '@/hooks/useJob';
import { useSaveJob } from '@/hooks/useSavedJobs';
import { useApplyJob } from '@/hooks/useApplications';
import { parseISO, format } from 'date-fns';
import { PageState } from '@/components/PageState';
import { Mark } from '@/components/Mark';

export function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const jobId = id ? parseInt(id, 10) : undefined;
  const navigate = useNavigate();
  const { user } = useAuth();
  const { data: job, isLoading, isError } = useJob(jobId);
  const { saveJob, isSaving }   = useSaveJob();
  const { applyJob, isApplying } = useApplyJob();
  const rootRef = useRef<HTMLDivElement>(null);

  const handleSave  = () => { if (!user) return navigate('/signin'); if (jobId) saveJob(jobId); };
  const handleApply = () => { if (!user) return navigate('/signin'); if (jobId) applyJob(jobId); };

  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add('(prefers-reduced-motion: no-preference)', () => {
      gsap.from('.jd-rise', { y: 16, autoAlpha: 0, duration: 0.6, stagger: 0.07, ease: 'power3.out' });
      gsap.fromTo('.mark-stroke',
        { strokeDasharray: 1, strokeDashoffset: 1 },
        { strokeDashoffset: 0, duration: 1.0, delay: 0.45, ease: 'power2.inOut' });
    });
    return () => mm.revert();
  }, { scope: rootRef, dependencies: [isLoading, job?.id] });

  if (isLoading) {
    return (
      <div className="page" ref={rootRef}>
        <PageState eyebrow="Signal" title="Loading signal…" body="Retrieving the full record for this role from the normalized index." />
      </div>
    );
  }
  if (isError || !job) {
    return (
      <div className="page" ref={rootRef}>
        <PageState tone="error" eyebrow="Signal" title="Signal not found." body="This role is no longer in the normalized index, or the reference is invalid. Return to the stream to pick up another signal." action={{ to: '/jobs', label: 'Back to all signals' }} />
      </div>
    );
  }

  const railCell: React.CSSProperties = { padding: '20px 24px', borderBottom: '1px solid var(--stone)' };
  const railLabel: React.CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.12em', color: 'var(--ink-muted)', marginBottom: 8 };

  return (
    <div className="page" ref={rootRef}>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 360px', flex: 1, minHeight: 0 }}>
        {/* Editorial article column */}
        <div style={{ display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--stone)', minWidth: 0 }}>
          {/* Breadcrumb band */}
          <div className="jd-rise" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 40px', borderBottom: '1px solid var(--stone)', background: 'var(--sand)', fontFamily: 'var(--font-mono)', fontSize: 12, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
            <Link to="/" style={{ fontWeight: 700, color: 'var(--cobalt)', textDecoration: 'none' }}>← Today</Link>
            <span style={{ color: 'var(--stone-dark)' }}>/</span>
            <Link to="/jobs" style={{ fontWeight: 700, color: 'var(--cobalt)', textDecoration: 'none' }}>Signals</Link>
            <span style={{ color: 'var(--stone-dark)' }}>/</span>
            <span style={{ color: 'var(--ink-muted)' }}>#{job.id}</span>
          </div>

          {/* Hero identity block */}
          <div style={{ position: 'relative', overflow: 'hidden', padding: '44px 40px 32px', borderBottom: '1px solid var(--stone)' }}>
            {/* Outline folio numeral, bled into the corner */}
            <span aria-hidden style={{ position: 'absolute', top: 8, right: 24, fontFamily: 'var(--font-mono)', fontSize: 'clamp(56px, 8vw, 116px)', fontWeight: 800, lineHeight: 1, letterSpacing: '-0.04em', color: 'transparent', WebkitTextStroke: '1px var(--stone)', pointerEvents: 'none', userSelect: 'none' }}>
              #{job.id}
            </span>

            <div className="jd-rise" style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18, position: 'relative' }}>
              <span style={{ width: 30, height: 2, background: 'var(--cobalt)', flexShrink: 0 }} />
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--cobalt)' }}>{job.source}</span>
              {job.published_at && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--stone-dark)', letterSpacing: '0.06em' }}>· {format(parseISO(job.published_at), 'd MMM yyyy')}</span>}
            </div>

            <h1 className="jd-rise" style={{ margin: '0 0 18px', fontSize: 'clamp(30px, 3.6vw, 52px)', fontWeight: 800, color: 'var(--ink)', lineHeight: 1.06, letterSpacing: '-0.03em', maxWidth: '20ch', position: 'relative' }}>
              {job.title}
            </h1>

            <div className="jd-rise" style={{ display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap', position: 'relative' }}>
              <span style={{ position: 'relative', display: 'inline-block', fontSize: 24, fontWeight: 800, color: 'var(--cobalt)' }}>
                {job.company}
                <Mark variant="underline" />
              </span>
              {job.location && <span style={{ fontSize: 18, color: 'var(--ink-muted)' }}>{job.location}</span>}
              {job.remote && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--acid)', border: '1px solid color-mix(in srgb, var(--acid) 40%, transparent)', padding: '3px 9px' }}>Remote</span>}
            </div>
          </div>

          {/* Actions */}
          <div className="jd-rise" style={{ display: 'flex', borderBottom: '1px solid var(--stone)' }}>
            <button onClick={handleSave} disabled={isSaving} style={{ flex: 1, padding: '16px 24px', fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', background: 'var(--sand)', border: 'none', borderRight: '1px solid var(--stone)', cursor: isSaving ? 'wait' : 'pointer', color: 'var(--ink)', transition: 'background 0.15s' }} onMouseOver={e => (e.currentTarget.style.background = '#E5DDD0')} onMouseOut={e => (e.currentTarget.style.background = 'var(--sand)')}>
              {isSaving ? 'Saving…' : '+ Save signal'}
            </button>
            <button onClick={handleApply} disabled={isApplying} style={{ flex: 1, padding: '16px 24px', fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', background: 'var(--cobalt)', border: 'none', cursor: isApplying ? 'wait' : 'pointer', color: '#fff', transition: 'opacity 0.15s' }}>
              {isApplying ? 'Marking…' : '✓ Mark applied'}
            </button>
            {job.url && (
              <a href={job.url} target="_blank" rel="noreferrer" style={{ flex: 1, padding: '16px 24px', fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', background: 'var(--sand)', color: 'var(--cobalt)', textDecoration: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', borderLeft: '1px solid var(--stone)' }}>
                Open source ↗
              </a>
            )}
          </div>

          {/* Description — bounded to a readable measure */}
          <div className="jd-rise" style={{ padding: '36px 40px', flex: 1 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.12em', color: 'var(--ink-muted)', marginBottom: 20 }}>
              Job Description
            </div>
            <div style={{ fontSize: 16, lineHeight: 1.75, color: 'var(--ink)', whiteSpace: 'pre-wrap', maxWidth: '72ch' }}>
              {job.description ?? <em style={{ color: 'var(--ink-muted)' }}>No description available for this signal.</em>}
            </div>
          </div>
        </div>

        {/* Metadata rail — bleeds to the viewport edge, coupled to the article */}
        <div style={{ background: 'var(--sand)', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <div style={railCell}>
            <div style={railLabel}>Signal ID</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 40, fontWeight: 800, color: 'var(--ink)', letterSpacing: '-0.04em' }}>#{job.id}</div>
          </div>
          <div style={railCell}>
            <div style={railLabel}>Source</div>
            <div style={{ fontSize: 17, fontWeight: 600, color: 'var(--cobalt)' }}>{job.source}</div>
          </div>
          <div style={railCell}>
            <div style={railLabel}>Employment</div>
            <div style={{ fontSize: 17, fontWeight: 600, color: 'var(--ink)' }}>{job.employment_type ?? 'Unknown'}</div>
          </div>
          <div style={railCell}>
            <div style={railLabel}>Discovered</div>
            <div style={{ fontSize: 16, color: 'var(--ink-soft)' }}>{format(parseISO(job.discovered_at), 'd MMM yyyy, HH:mm')}</div>
          </div>
          {(job.salary_min || job.salary_max) ? (
            <div style={{ padding: '24px', background: 'var(--cobalt)' }}>
              <div style={{ ...railLabel, color: 'rgba(255,255,255,0.6)' }}>Compensation</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 24, fontWeight: 800, color: '#fff', lineHeight: 1.3 }}>
                {[job.salary_min, job.salary_max].filter(Boolean).map(v => v!.toLocaleString()).join(' – ')}
                {job.currency ? ` ${job.currency}` : ''}
              </div>
            </div>
          ) : (
            <div style={railCell}>
              <div style={railLabel}>Compensation</div>
              <div style={{ fontSize: 16, color: 'var(--stone-dark)' }}>Not disclosed</div>
            </div>
          )}
          {/* Structural fill so the rail reaches the viewport edge */}
          <div className="hatch" style={{ flex: 1, minHeight: 40 }} />
        </div>
      </div>
    </div>
  );
}
