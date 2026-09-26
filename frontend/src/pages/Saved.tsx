import { Link, Navigate } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useSavedJobs, useUnsaveJob } from '@/hooks/useSavedJobs';

export function Saved() {
  const { user, isLoading: authLoading } = useAuth();
  const { data: savedJobs, isLoading, isError } = useSavedJobs();
  const { unsaveJob, isUnsaving } = useUnsaveJob();

  if (authLoading) return null;
  if (!user) return <Navigate to="/signin" replace />;
  if (isLoading) return <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--ink-muted)' }}>Loading…</div>;
  if (isError)   return <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--vermillion)' }}>Error loading saved signals</div>;

  return (
    <div style={{ minHeight: 'calc(100vh - 52px)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '24px 32px 20px', borderBottom: '1px solid var(--stone)', background: 'var(--sand)', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <h1 style={{ margin: 0, fontSize: 32, fontWeight: 800, letterSpacing: '-0.02em' }}>Saved Signals</h1>
        <span style={{ fontSize: 17, color: 'var(--ink-muted)' }}>{savedJobs?.items.length ?? 0} saved</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 160px 100px', borderBottom: '1px solid var(--stone)', background: 'var(--sand)' }}>
        {['Role & Company', 'Location', ''].map((h, i) => (
          <div key={i} style={{
            padding: '10px 20px', fontSize: 13, fontWeight: 700,
            textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--ink-muted)',
            borderLeft: i > 0 ? '1px solid var(--stone)' : 'none',
          }}>{h}</div>
        ))}
      </div>

      {!savedJobs?.items.length && (
        <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--ink-muted)' }}>No saved signals yet.</div>
      )}

      {savedJobs?.items.map(saved => (
        <div
          key={saved.id}
          className="signal-entry"
          style={{ display: 'grid', gridTemplateColumns: '1fr 160px 100px' }}
        >
          <div style={{ padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <Link
              to={`/jobs/${saved.job.id}`}
              style={{ fontSize: 18, fontWeight: 600, color: 'var(--ink)', textDecoration: 'none' }}
              onMouseOver={e => (e.currentTarget.style.color = 'var(--cobalt)')}
              onMouseOut={e => (e.currentTarget.style.color = 'var(--ink)')}
            >
              {saved.job.title}
            </Link>
            <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--cobalt)' }}>{saved.job.company}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', padding: '18px 16px', borderLeft: '1px solid var(--stone)' }}>
            <span style={{ fontSize: 15, color: 'var(--ink-muted)' }}>
              {saved.job.remote ? 'Remote' : saved.job.location ?? '—'}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', padding: '18px 16px', borderLeft: '1px solid var(--stone)' }}>
            <button
              onClick={() => unsaveJob(saved.job.id)}
              disabled={isUnsaving}
              style={{ fontSize: 15, fontWeight: 600, color: 'var(--vermillion)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
            >
              Remove
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
