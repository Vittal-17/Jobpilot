import { Link, Navigate } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useApplications, useUpdateApplication } from '@/hooks/useApplications';
import { parseISO, format } from 'date-fns';
import type { ApplicationStatus } from '@/api/types';

const STATUS_COLOR: Record<string, string> = {
  applied: 'var(--cobalt)',
  interviewing: 'var(--amber)',
  offer: 'var(--mint)',
  rejected: 'var(--vermillion)',
  withdrawn: 'var(--stone-dark)',
};

const STATUS_OPTIONS: ApplicationStatus[] = [
  'applied', 'interviewing', 'offer', 'rejected', 'withdrawn'
];

export function Applications() {
  const { user, isLoading: authLoading } = useAuth();
  const { data: applications, isLoading, isError } = useApplications();
  const { updateApplication } = useUpdateApplication();

  if (authLoading) return null;
  if (!user) return <Navigate to="/signin" replace />;
  if (isLoading) return <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--ink-muted)' }}>Loading…</div>;
  if (isError)   return <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--vermillion)' }}>Error loading applications</div>;

  return (
    <div style={{ minHeight: 'calc(100vh - 52px)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '24px 32px 20px', borderBottom: '1px solid var(--stone)', background: 'var(--sand)', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <h1 style={{ margin: 0, fontSize: 32, fontWeight: 800, letterSpacing: '-0.02em' }}>Applications</h1>
        <span style={{ fontSize: 17, color: 'var(--ink-muted)' }}>{applications?.items.length ?? 0} tracked</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 140px 140px 100px', borderBottom: '1px solid var(--stone)', background: 'var(--sand)' }}>
        {['Role & Company', 'Location', 'Status', 'Date'].map((h, i) => (
          <div key={h} style={{
            padding: '10px 20px', fontSize: 13, fontWeight: 700,
            textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--ink-muted)',
            borderLeft: i > 0 ? '1px solid var(--stone)' : 'none',
          }}>{h}</div>
        ))}
      </div>

      {!applications?.items.length && (
        <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--ink-muted)' }}>No applications tracked.</div>
      )}

      {applications?.items.map(app => (
        <div
          key={app.id}
          className="signal-entry"
          style={{ display: 'grid', gridTemplateColumns: '1fr 140px 140px 100px' }}
        >
          <div style={{ padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <Link
              to={`/jobs/${app.job.id}`}
              style={{ fontSize: 18, fontWeight: 600, color: 'var(--ink)', textDecoration: 'none' }}
              onMouseOver={e => (e.currentTarget.style.color = 'var(--cobalt)')}
              onMouseOut={e => (e.currentTarget.style.color = 'var(--ink)')}
            >
              {app.job.title}
            </Link>
            <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--cobalt)' }}>{app.job.company}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', padding: '18px 16px', borderLeft: '1px solid var(--stone)' }}>
            <span style={{ fontSize: 15, color: 'var(--ink-muted)' }}>
              {app.job.remote ? 'Remote' : app.job.location ?? '—'}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', padding: '18px 16px', borderLeft: '1px solid var(--stone)' }}>
            <select
              value={app.status}
              onChange={(e) => updateApplication({ id: app.id, status: e.target.value as ApplicationStatus })}
              style={{
                fontSize: 15,
                fontWeight: 700,
                color: STATUS_COLOR[app.status] ?? 'var(--ink)',
                background: 'transparent',
                border: 'none',
                outline: 'none',
                cursor: 'pointer',
                padding: 0,
                appearance: 'none'
              }}
            >
              {STATUS_OPTIONS.map(status => (
                <option key={status} value={status} style={{ color: 'var(--ink)' }}>
                  {status}
                </option>
              ))}
            </select>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', padding: '18px 16px', borderLeft: '1px solid var(--stone)' }}>
            <span style={{ fontSize: 15, color: 'var(--stone-dark)' }}>
              {format(parseISO(app.created_at), 'd MMM')}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
