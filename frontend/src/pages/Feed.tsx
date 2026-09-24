import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useRecommendations } from '@/hooks/useRecommendations';

export function Feed() {
  const [page, setPage] = useState(1);
  const size = 20;

  const { data: recs, isLoading, isError } = useRecommendations(page, size);

  if (isLoading) {
    return <div className="max-w-5xl mx-auto animate-pulse h-32 bg-[var(--color-border)]"></div>;
  }

  if (isError) {
    return <div className="max-w-5xl mx-auto text-[var(--color-signal-error)]">Failed to load feed.</div>;
  }

  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="font-serif text-4xl mb-8">Recommendation Feed</h1>

      {!recs?.items.length ? (
        <div className="text-sm text-[var(--color-text-secondary)] italic">No recommendations found.</div>
      ) : (
        <div className="space-y-6">
          {recs.items.map(({ job, match, recommended_at }) => (
            <div key={job.id} className="p-6 border border-[var(--color-border)] hover:border-[var(--color-text-primary)] transition-colors bg-white/40">
              <div className="flex justify-between items-start mb-2">
                <Link to={`/jobs/${job.id}`} className="font-serif text-2xl hover:underline">
                  {job.title}
                </Link>
                <div className="text-xs text-[var(--color-text-secondary)] whitespace-nowrap ml-4">
                  {new Date(recommended_at).toLocaleDateString()}
                </div>
              </div>
              <div className="text-sm mb-4">
                {job.company} {job.location ? `— ${job.location}` : ''}
              </div>

              <div className="text-xs p-3 bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
                {match ? (
                  <div className="flex gap-4 items-center">
                    <span className="font-medium">Score: {match.score !== null ? `${match.score}%` : 'N/A'}</span>
                    <span className="text-[var(--color-text-secondary)] truncate">
                      {match.reasons?.map(r => r.message).join(' • ')}
                    </span>
                  </div>
                ) : (
                  <span className="text-[var(--color-text-secondary)] italic">Historical match details unavailable.</span>
                )}
              </div>
            </div>
          ))}

          <div className="flex justify-between items-center pt-8 border-t border-[var(--color-border)]">
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page === 1}
              className="px-4 py-2 text-sm border border-[var(--color-border)] disabled:opacity-50 hover:bg-[var(--color-bg-secondary)]"
            >
              Previous
            </button>
            <span className="text-sm text-[var(--color-text-secondary)]">
              Page {page} of {Math.ceil((recs?.total || 0) / size)}
            </span>
            <button
              onClick={() => setPage(p => p + 1)}
              disabled={page >= Math.ceil((recs?.total || 0) / size)}
              className="px-4 py-2 text-sm border border-[var(--color-border)] disabled:opacity-50 hover:bg-[var(--color-bg-secondary)]"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
