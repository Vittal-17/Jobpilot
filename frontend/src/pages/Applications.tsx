import { Link, Navigate } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useApplications, useUpdateApplication } from '@/hooks/useApplications';
import { parseISO, format, formatDistanceToNow } from 'date-fns';
import type { ApplicationStatus } from '@/api/types';
import { PageState } from '@/components/PageState';
import { SectionHead } from '@/components/SectionHead';
import { Mark } from '@/components/Mark';

const STATUS_COLOR: Record<string, string> = {
  applied: 'var(--cobalt)',
  interviewing: 'var(--amber)',
  offer: 'var(--mint)',
  rejected: 'var(--vermillion)',
  withdrawn: 'var(--stone-dark)',
};

const STATUS_OPTIONS: ApplicationStatus[] = ['applied', 'interviewing', 'offer', 'rejected', 'withdrawn'];
const COLS = 'minmax(0, 1fr) 200px 160px 130px 110px';

export function Applications() {
  const { user, isLoading: authLoading } = useAuth();
  const { data: applications, isLoading, isError } = useApplications();
  const { updateApplication } = useUpdateApplication();

  if (authLoading) return null;
  if (!user) return <Navigate to="/signin" replace />;

  const items = applications?.items ?? [];
  const counts = STATUS_OPTIONS.map(s => ({ status: s, n: items.filter(a => a.status === s).length }));

  const body = (() => {
    if (isLoading) {
      return <PageState eyebrow="Tracker" title="Loading applications…" body="Retrieving the roles you're actively pursuing and their current status." />;
    }
    if (isError) {
      return <PageState tone="error" eyebrow="Tracker" title="Couldn't load applications." body="Your application tracker is stored server-side and could not be retrieved. Try again once the connection settles." />;
    }
    if (items.length === 0) {
      return (
        <PageState
          eyebrow="Tracker"
          title="No applications tracked yet."
          body="When you mark a signal as applied, it enters this tracker so you can follow it from application through to an offer or close — and update its status as things progress."
          action={{ to: '/jobs', label: 'Find roles to apply to' }}
          motif={STATUS_OPTIONS.map(s => ({ label: s.toUpperCase(), color: STATUS_COLOR[s] }))}
        />
      );
    }
    return (
      <>
        {/* Status distribution — real counts only, no fabricated stats */}
        <div style={{ display: 'flex', borderBottom: '1px solid var(--stone)', background: 'var(--cream)' }}>
          {counts.map(({ status, n }, i) => (
            <div key={status} style={{ flex: 1, padding: '18px 20px', borderLeft: i > 0 ? '1px solid var(--stone)' : 'none', borderTop: `3px solid ${n ? STATUS_COLOR[status] : 'var(--stone)'}` }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 30, fontWeight: 800, lineHeight: 1, letterSpacing: '-0.03em', color: n ? STATUS_COLOR[status] : 'var(--stone-dark)' }}>{n}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.12em', color: 'var(--ink-muted)', marginTop: 9 }}>{status}</div>
            </div>
          ))}
        </div>

        <div className="grid-head" style={{ gridTemplateColumns: COLS }}>
          {['Role & Company', 'Location', 'Status', 'Applied', 'Updated'].map(h => (
            <div key={h}>{h}</div>
          ))}
        </div>

        {items.map(app => (
          <div key={app.id} className="signal-entry" style={{ display: 'grid', gridTemplateColumns: COLS, cursor: 'default' }}>
            <div style={{ padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 4, borderRight: '1px solid var(--stone)', borderLeft: `3px solid ${STATUS_COLOR[app.status] ?? 'var(--stone)'}` }}>
              <Link to={`/jobs/${app.job.id}`} style={{ fontSize: 18, fontWeight: 600, color: 'var(--ink)', textDecoration: 'none' }} onMouseOver={e => (e.currentTarget.style.color = 'var(--cobalt)')} onMouseOut={e => (e.currentTarget.style.color = 'var(--ink)')}>
                {app.job.title}
              </Link>
              <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--cobalt)' }}>{app.job.company}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', padding: '18px 20px', borderRight: '1px solid var(--stone)' }}>
              <span style={{ fontSize: 15, color: 'var(--ink-muted)' }}>{app.job.remote ? 'Remote' : app.job.location ?? '—'}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', padding: '18px 20px', borderRight: '1px solid var(--stone)' }}>
              <select
                value={app.status}
                onChange={(e) => updateApplication({ id: app.id, status: e.target.value as ApplicationStatus })}
                style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: STATUS_COLOR[app.status] ?? 'var(--ink)', background: 'transparent', border: 'none', outline: 'none', cursor: 'pointer', padding: 0, appearance: 'none' }}
              >
                {STATUS_OPTIONS.map(status => (
                  <option key={status} value={status} style={{ color: 'var(--ink)' }}>{status}</option>
                ))}
              </select>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', padding: '18px 20px', borderRight: '1px solid var(--stone)' }}>
              <span style={{ fontSize: 14, color: 'var(--stone-dark)' }}>{format(parseISO(app.created_at), 'd MMM yyyy')}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', padding: '18px 20px' }}>
              <span style={{ fontSize: 14, color: 'var(--stone-dark)' }}>{formatDistanceToNow(parseISO(app.updated_at))} ago</span>
            </div>
          </div>
        ))}
        <div className="hatch" style={{ flex: 1, borderTop: '1px solid var(--stone)' }} />
      </>
    );
  })();

  return (
    <div className="page">
      <SectionHead
        index="05"
        kicker="Tracker"
        title={<>Every pursuit, <em>in play<Mark variant="underline" /></em>.</>}
        deck="Applications you're following from first contact through to an offer or a close — status yours to update as things move."
        aside={<><span className="u">Tracked</span><span>{items.length}</span></>}
      />
      {body}
    </div>
  );
}
