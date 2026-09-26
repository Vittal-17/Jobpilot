import { Link, Navigate } from 'react-router-dom';
import { formatDistanceToNow, parseISO } from 'date-fns';
import { useAuth } from '@/hooks/useAuth';
import { useSavedJobs, useUnsaveJob } from '@/hooks/useSavedJobs';
import { PageState } from '@/components/PageState';
import { SectionHead } from '@/components/SectionHead';
import { Mark } from '@/components/Mark';

const COLS = 'minmax(0, 1fr) 220px 150px 120px';

export function Saved() {
  const { user, isLoading: authLoading } = useAuth();
  const { data: savedJobs, isLoading, isError } = useSavedJobs();
  const { unsaveJob, isUnsaving } = useUnsaveJob();

  if (authLoading) return null;
  if (!user) return <Navigate to="/signin" replace />;

  const items = savedJobs?.items ?? [];

  const body = (() => {
    if (isLoading) {
      return <PageState eyebrow="Shortlist" title="Loading your shortlist…" body="Retrieving the roles you've set aside for closer review." />;
    }
    if (isError) {
      return <PageState tone="error" eyebrow="Shortlist" title="Couldn't load saved signals." body="Your shortlist is stored server-side and could not be retrieved. Try again once the connection settles." />;
    }
    if (items.length === 0) {
      return (
        <PageState
          eyebrow="Shortlist"
          title="Your workbench is empty."
          body="Saved signals are roles you've pulled out of the stream to weigh before applying. Open any signal and save it to build a shortlist here."
          action={{ to: '/jobs', label: 'Browse all signals' }}
          motif={[
            { label: 'SAVE FROM DETAIL', color: 'var(--cobalt)' },
            { label: 'WEIGH & COMPARE', color: 'var(--amber)' },
            { label: 'MARK APPLIED', color: 'var(--mint)' },
          ]}
        />
      );
    }
    return (
      <>
        <div className="grid-head" style={{ gridTemplateColumns: COLS }}>
          {['Role & Company', 'Location', 'Saved', ''].map((h, i) => (
            <div key={i}>{h || ' '}</div>
          ))}
        </div>

        {items.map(saved => (
          <div key={saved.id} className="signal-entry" style={{ display: 'grid', gridTemplateColumns: COLS, cursor: 'default' }}>
            <div style={{ padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 4, borderRight: '1px solid var(--stone)' }}>
              <Link to={`/jobs/${saved.job.id}`} style={{ fontSize: 18, fontWeight: 600, color: 'var(--ink)', textDecoration: 'none' }} onMouseOver={e => (e.currentTarget.style.color = 'var(--cobalt)')} onMouseOut={e => (e.currentTarget.style.color = 'var(--ink)')}>
                {saved.job.title}
              </Link>
              <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--cobalt)' }}>{saved.job.company}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', padding: '18px 20px', borderRight: '1px solid var(--stone)' }}>
              <span style={{ fontSize: 15, color: 'var(--ink-muted)' }}>{saved.job.remote ? 'Remote' : saved.job.location ?? '—'}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', padding: '18px 20px', borderRight: '1px solid var(--stone)' }}>
              <span style={{ fontSize: 14, color: 'var(--stone-dark)' }}>{formatDistanceToNow(parseISO(saved.saved_at))} ago</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', padding: '18px 20px' }}>
              <button onClick={() => unsaveJob(saved.job.id)} disabled={isUnsaving} style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--vermillion)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
                Remove
              </button>
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
        index="04"
        kicker="Shortlist"
        title={<>The <em>workbench<Mark variant="ring" /></em>.</>}
        deck="Roles pulled out of the stream and set aside to weigh before you commit."
        aside={<><span className="u">Saved</span><span>{items.length}</span></>}
      />
      {body}
    </div>
  );
}
