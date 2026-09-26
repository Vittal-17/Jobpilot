import { useParams, Link, useNavigate } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useJob } from '@/hooks/useJob';
import { useSaveJob } from '@/hooks/useSavedJobs';
import { useApplyJob } from '@/hooks/useApplications';
import { parseISO, format } from 'date-fns';

export function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const jobId = id ? parseInt(id, 10) : undefined;
  const navigate = useNavigate();
  const { user } = useAuth();
  const { data: job, isLoading, isError } = useJob(jobId);
  const { saveJob, isSaving }   = useSaveJob();
  const { applyJob, isApplying } = useApplyJob();

  const handleSave  = () => { if (!user) return navigate('/signin'); if (jobId) saveJob(jobId); };
  const handleApply = () => { if (!user) return navigate('/signin'); if (jobId) applyJob(jobId); };

  if (isLoading) return <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--ink-muted)' }}>Loading…</div>;
  if (isError || !job) return <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--vermillion)' }}>Signal not found</div>;

  return (
    <div style={{ display: 'flex', minHeight: 'calc(100vh - 52px)' }}>

      {/* Left: main content */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--stone)' }}>

        {/* Breadcrumb */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '14px 28px',
          borderBottom: '1px solid var(--stone)', background: 'var(--sand)',
        }}>
          <Link to="/" style={{ fontSize: 15, fontWeight: 600, color: 'var(--cobalt)', textDecoration: 'none' }}>← Today</Link>
          <span style={{ color: 'var(--stone)', fontSize: 18 }}>·</span>
          <span style={{ fontSize: 15, color: 'var(--ink-muted)' }}>Signal #{job.id}</span>
        </div>

        {/* Hero identity block */}
        <div style={{
          padding: '36px 32px 28px',
          borderBottom: '1px solid var(--stone)',
          background: 'var(--cream)',
        }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-muted)' }}>
              {job.source}
            </span>
            {job.published_at && (
              <span style={{ fontSize: 13, color: 'var(--stone-dark)' }}>· {format(parseISO(job.published_at), 'd MMM yyyy')}</span>
            )}
          </div>

          <h1 style={{
            margin: 0, fontSize: 'clamp(28px, 3.5vw, 48px)', fontWeight: 800,
            color: 'var(--ink)', lineHeight: 1.15, letterSpacing: '-0.02em',
            marginBottom: 16,
          }}>
            {job.title}
          </h1>

          <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 24, fontWeight: 800, color: 'var(--cobalt)' }}>{job.company}</span>
            {job.location && <span style={{ fontSize: 18, color: 'var(--ink-muted)' }}>{job.location}</span>}
            {job.remote && (
              <span style={{
                fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em',
                color: 'var(--acid)', background: 'rgba(140,168,0,0.12)', padding: '3px 10px',
              }}>
                Remote
              </span>
            )}
          </div>
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', borderBottom: '1px solid var(--stone)' }}>
          <button onClick={handleSave} disabled={isSaving} style={{
            flex: 1, padding: '16px 24px', fontSize: 16, fontWeight: 600,
            background: 'var(--sand)', border: 'none', borderRight: '1px solid var(--stone)',
            cursor: isSaving ? 'wait' : 'pointer', color: 'var(--ink)',
            transition: 'background 0.15s',
          }}
          onMouseOver={e => (e.currentTarget.style.background = '#E5DDD0')}
          onMouseOut={e => (e.currentTarget.style.background = 'var(--sand)')}
          >
            {isSaving ? 'Saving…' : '+ Save signal'}
          </button>
          <button onClick={handleApply} disabled={isApplying} style={{
            flex: 1, padding: '16px 24px', fontSize: 16, fontWeight: 600,
            background: 'var(--cobalt)', border: 'none', borderRight: job.url ? '1px solid rgba(255,255,255,0.2)' : 'none',
            cursor: isApplying ? 'wait' : 'pointer', color: '#fff',
            transition: 'opacity 0.15s',
          }}>
            {isApplying ? 'Marking…' : '✓ Mark applied'}
          </button>
          {job.url && (
            <a href={job.url} target="_blank" rel="noreferrer" style={{
              flex: 1, padding: '16px 24px', fontSize: 16, fontWeight: 600,
              background: 'var(--sand)', color: 'var(--cobalt)', textDecoration: 'none',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              borderLeft: '1px solid var(--stone)',
            }}>
              Open source ↗
            </a>
          )}
        </div>

        {/* Metadata grid */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
          borderBottom: '1px solid var(--stone)',
        }}>
          {[
            { label: 'Type',       value: job.employment_type ?? 'Unknown' },
            { label: 'Remote',     value: job.remote ? 'Yes' : 'No' },
            { label: 'Salary Min', value: job.salary_min ? `${job.salary_min.toLocaleString()} ${job.currency ?? ''}` : '—' },
            { label: 'Salary Max', value: job.salary_max ? `${job.salary_max.toLocaleString()} ${job.currency ?? ''}` : '—' },
          ].map(({ label, value }) => (
            <div key={label} style={{
              padding: '18px 20px', borderRight: '1px solid var(--stone)', background: 'var(--sand)',
            }}>
              <div style={{ fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--ink-muted)', marginBottom: 6 }}>
                {label}
              </div>
              <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>{value}</div>
            </div>
          ))}
        </div>

        {/* Description */}
        <div style={{ padding: '32px', flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-muted)', marginBottom: 20 }}>
            Job Description
          </div>
          <div style={{
            fontSize: 16, lineHeight: 1.75, color: 'var(--ink)',
            whiteSpace: 'pre-wrap', maxWidth: '70ch',
          }}>
            {job.description ?? <em style={{ color: 'var(--ink-muted)' }}>No description available</em>}
          </div>
        </div>
      </div>

      {/* Right: metadata sidebar */}
      <div style={{ width: 240, flexShrink: 0, background: 'var(--sand)', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '20px 20px', borderBottom: '1px solid var(--stone)' }}>
          <div style={{ fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-muted)', marginBottom: 10 }}>
            Signal ID
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 40, fontWeight: 800, color: 'var(--ink)', letterSpacing: '-0.04em' }}>
            #{job.id}
          </div>
        </div>
        <div style={{ padding: '20px 20px', borderBottom: '1px solid var(--stone)' }}>
          <div style={{ fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-muted)', marginBottom: 6 }}>
            Source
          </div>
          <div style={{ fontSize: 17, fontWeight: 600, color: 'var(--cobalt)' }}>{job.source}</div>
        </div>
        <div style={{ padding: '20px 20px', borderBottom: '1px solid var(--stone)' }}>
          <div style={{ fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-muted)', marginBottom: 6 }}>
            Discovered
          </div>
          <div style={{ fontSize: 16, color: 'var(--ink-soft)' }}>
            {format(parseISO(job.discovered_at), 'd MMM yyyy, HH:mm')}
          </div>
        </div>
        {(job.salary_min || job.salary_max) && (
          <div style={{ padding: '20px 20px', background: 'var(--cobalt)', borderTop: '2px solid var(--cobalt)' }}>
            <div style={{ fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'rgba(255,255,255,0.6)', marginBottom: 8 }}>
              Compensation
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 800, color: '#fff', lineHeight: 1.3 }}>
              {[job.salary_min, job.salary_max].filter(Boolean).map(v => v!.toLocaleString()).join(' – ')}
              {job.currency ? ` ${job.currency}` : ''}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
