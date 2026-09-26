import { useState } from 'react';
import { Link } from 'react-router-dom';
import { formatDistanceToNow, parseISO } from 'date-fns';
import { useRecommendations } from '@/hooks/useRecommendations';

function scoreColor(s: number | null | undefined) {
  if (s == null) return 'var(--stone-dark)';
  return s >= 80 ? 'var(--cobalt)' : s >= 60 ? 'var(--amber)' : 'var(--stone-dark)';
}

export function Feed() {
  const [page, setPage] = useState(1);
  const size = 25;
  const { data: recs, isLoading, isError } = useRecommendations(page, size);
  const totalPages = Math.ceil((recs?.total ?? 0) / size);

  return (
    <div style={{ minHeight: 'calc(100vh - 52px)', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{
        padding: '24px 32px 20px',
        borderBottom: '1px solid var(--stone)',
        background: 'var(--sand)',
        display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
      }}>
        <h1 style={{ margin: 0, fontSize: 32, fontWeight: 800, color: 'var(--ink)', letterSpacing: '-0.02em' }}>
          All Signals
        </h1>
        <span style={{ fontSize: 17, color: 'var(--ink-muted)' }}>
          {recs?.total ?? '…'} total
        </span>
      </div>

      {/* Column header */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '80px 1fr 160px 100px 90px',
        borderBottom: '1px solid var(--stone)',
        background: 'var(--sand)',
      }}>
        {['Score', 'Role & Company', 'Location', 'Type', 'Age'].map((h, i) => (
          <div key={h} style={{
            padding: '10px 16px', fontSize: 13, fontWeight: 700, textTransform: 'uppercase',
            letterSpacing: '0.06em', color: 'var(--ink-muted)',
            borderLeft: i > 0 ? '1px solid var(--stone)' : 'none',
          }}>
            {h}
          </div>
        ))}
      </div>

      {isLoading && <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--ink-muted)' }}>Loading…</div>}
      {isError   && <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--vermillion)' }}>Connection error</div>}
      {!isLoading && !isError && recs?.items.length === 0 && (
        <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--ink-muted)' }}>No signals available</div>
      )}

      {!isLoading && recs?.items.map(({ job, match, recommended_at }) => (
        <Link
          key={job.id}
          to={`/jobs/${job.id}`}
          className="signal-entry"
          style={{
            display: 'grid',
            gridTemplateColumns: '80px 1fr 160px 100px 90px',
            textDecoration: 'none',
            color: 'inherit',
          }}
        >
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: '14px 8px',
            borderRight: '1px solid var(--stone)',
          }}>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 800, lineHeight: 1,
              color: scoreColor(match?.score), letterSpacing: '-0.03em',
            }}>
              {match?.score ?? '—'}
            </span>
          </div>

          <div style={{ padding: '14px 20px', borderRight: '1px solid var(--stone)', minWidth: 0 }}>
            <div style={{ fontSize: 17, fontWeight: 600, color: 'var(--ink)', marginBottom: 3, lineHeight: 1.3 }}>
              {job.title}
            </div>
            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--cobalt)' }}>
              {job.company}
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', padding: '14px 16px', borderRight: '1px solid var(--stone)' }}>
            <span style={{ fontSize: 14, color: 'var(--ink-muted)' }}>
              {job.remote ? 'Remote' : job.location ?? '—'}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', padding: '14px 16px', borderRight: '1px solid var(--stone)' }}>
            <span style={{ fontSize: 14, color: 'var(--ink-muted)' }}>{job.employment_type ?? '—'}</span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', padding: '14px 16px' }}>
            <span style={{ fontSize: 14, color: 'var(--stone-dark)' }}>
              {formatDistanceToNow(parseISO(recommended_at))}
            </span>
          </div>
        </Link>
      ))}

      {/* Pagination */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '20px 32px', marginTop: 'auto',
        borderTop: '1px solid var(--stone)', background: 'var(--sand)',
      }}>
        <button
          onClick={() => setPage(p => Math.max(1, p - 1))}
          disabled={page === 1}
          style={{
            fontSize: 16, fontWeight: 600, color: page === 1 ? 'var(--stone)' : 'var(--ink)',
            background: 'none', border: 'none', cursor: page === 1 ? 'default' : 'pointer',
          }}
        >
          ← Previous
        </button>
        <span style={{ fontSize: 16, color: 'var(--ink-muted)' }}>
          Page {page} of {totalPages || 1}
        </span>
        <button
          onClick={() => setPage(p => p + 1)}
          disabled={page >= totalPages}
          style={{
            fontSize: 16, fontWeight: 600, color: page >= totalPages ? 'var(--stone)' : 'var(--ink)',
            background: 'none', border: 'none', cursor: page >= totalPages ? 'default' : 'pointer',
          }}
        >
          Next →
        </button>
      </div>
    </div>
  );
}
