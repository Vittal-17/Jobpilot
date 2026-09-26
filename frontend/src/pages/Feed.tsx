import { useState } from 'react';
import { Link } from 'react-router-dom';
import { formatDistanceToNow, parseISO } from 'date-fns';
import { useRecommendations } from '@/hooks/useRecommendations';
import { PageState } from '@/components/PageState';
import { SectionHead } from '@/components/SectionHead';
import { Mark } from '@/components/Mark';

function scoreColor(s: number | null | undefined) {
  if (s == null) return 'var(--stone-dark)';
  return s >= 80 ? 'var(--cobalt)' : s >= 60 ? 'var(--amber)' : 'var(--stone-dark)';
}

const COLS = '96px minmax(0, 1fr) 200px 150px 120px 110px';

export function Feed() {
  const [page, setPage] = useState(1);
  const size = 25;
  const { data: recs, isLoading, isError } = useRecommendations(page, size);
  const totalPages = Math.ceil((recs?.total ?? 0) / size);
  const items = recs?.items ?? [];

  const body = (() => {
    if (isLoading) {
      return <PageState eyebrow="All signals" title="Loading the stream…" body="Recommendations scored against your active vectors are being retrieved from the engine." />;
    }
    if (isError) {
      return <PageState tone="error" eyebrow="All signals" title="The stream is unreachable." body="The recommendation engine did not respond. Surfaced roles will reappear here the moment contact is restored." action={{ to: '/system', label: 'Inspect system state' }} />;
    }
    if (items.length === 0) {
      return <PageState eyebrow="All signals" title="Nothing has surfaced yet." body="No roles have cleared the match threshold. Discovery, normalization and enrichment keep running — the archive fills as signals qualify." action={{ to: '/search', label: 'Tune the search vectors' }} />;
    }
    return (
      <>
        {/* Column header — spans full viewport */}
        <div className="grid-head" style={{ gridTemplateColumns: COLS }}>
          {['Score', 'Role & Company', 'Signal', 'Location', 'Type', 'Age'].map(h => (
            <div key={h}>{h}</div>
          ))}
        </div>

        {items.map(({ job, match, recommended_at }) => (
          <Link key={job.id} to={`/jobs/${job.id}`} className="signal-entry" style={{ display: 'grid', gridTemplateColumns: COLS, textDecoration: 'none', color: 'inherit', alignItems: 'stretch' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px 8px', borderRight: '1px solid var(--stone)', borderLeft: `3px solid ${scoreColor(match?.score)}` }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 30, fontWeight: 800, lineHeight: 1, color: scoreColor(match?.score), letterSpacing: '-0.03em' }}>
                {match?.score ?? '—'}
              </span>
            </div>

            <div style={{ padding: '15px 20px', borderRight: '1px solid var(--stone)', minWidth: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
              <div style={{ fontSize: 17, fontWeight: 600, color: 'var(--ink)', marginBottom: 3, lineHeight: 1.3 }}>{job.title}</div>
              <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--cobalt)' }}>{job.company}</div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', padding: '15px 20px', borderRight: '1px solid var(--stone)', minWidth: 0 }}>
              <span style={{ fontSize: 14, color: 'var(--ink-soft)', lineHeight: 1.4, overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                {match?.reasons?.[0]?.message ?? <span style={{ color: 'var(--stone-dark)' }}>—</span>}
              </span>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', padding: '15px 20px', borderRight: '1px solid var(--stone)' }}>
              <span style={{ fontSize: 14, color: 'var(--ink-muted)' }}>{job.remote ? 'Remote' : job.location ?? '—'}</span>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', padding: '15px 20px', borderRight: '1px solid var(--stone)' }}>
              <span style={{ fontSize: 14, color: 'var(--ink-muted)' }}>{job.employment_type ?? '—'}</span>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', padding: '15px 20px' }}>
              <span style={{ fontSize: 14, color: 'var(--stone-dark)' }}>{formatDistanceToNow(parseISO(recommended_at))}</span>
            </div>
          </Link>
        ))}

        {/* Pagination — full-width footer band */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '20px 40px', marginTop: 'auto', borderTop: '1px solid var(--stone)', background: 'var(--sand)' }}>
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: page === 1 ? 'var(--stone)' : 'var(--ink)', background: 'none', border: 'none', cursor: page === 1 ? 'default' : 'pointer' }}>
            ← Previous
          </button>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--ink-muted)' }}>Page {page} of {totalPages || 1}</span>
          <button onClick={() => setPage(p => p + 1)} disabled={page >= totalPages} style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: page >= totalPages ? 'var(--stone)' : 'var(--ink)', background: 'none', border: 'none', cursor: page >= totalPages ? 'default' : 'pointer' }}>
            Next →
          </button>
        </div>
      </>
    );
  })();

  return (
    <div className="page">
      <SectionHead
        index="02"
        kicker="The Stream"
        title={<>Every signal, <em>ranked<Mark variant="swoop" /></em>.</>}
        deck="The full archive of roles the engine has surfaced, each scored against your active vectors — strongest match first."
        aside={<><span className="u">Surfaced</span><span>{recs ? recs.total.toLocaleString() : '…'}</span></>}
      />
      {body}
    </div>
  );
}
