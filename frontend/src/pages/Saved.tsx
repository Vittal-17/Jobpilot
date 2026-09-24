import { Link } from 'react-router-dom';
import { useSavedJobs, useUnsaveJob } from '@/hooks/useSavedJobs';

export function Saved() {
  const { data: savedJobs, isLoading, isError } = useSavedJobs();
  const { unsaveJob, isUnsaving } = useUnsaveJob();

  if (isLoading) {
    return <div className="max-w-4xl mx-auto animate-pulse h-32 bg-[var(--color-border)]"></div>;
  }

  if (isError) {
    return <div className="max-w-4xl mx-auto text-[var(--color-signal-error)]">Failed to load saved jobs.</div>;
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="font-serif text-4xl mb-8">Saved Jobs</h1>

      {!savedJobs?.items.length ? (
        <div className="text-sm text-[var(--color-text-secondary)] italic">No saved jobs yet.</div>
      ) : (
        <div className="space-y-4">
          {savedJobs.items.map((saved) => (
            <div key={saved.id} className="p-6 border border-[var(--color-border)] hover:border-[var(--color-text-primary)] transition-colors flex justify-between items-center bg-white/40">
              <div>
                <Link to={`/jobs/${saved.job.id}`} className="font-serif text-xl block mb-1 hover:underline">
                  {saved.job.title}
                </Link>
                <div className="text-sm text-[var(--color-text-secondary)]">
                  {saved.job.company} {saved.job.location ? `— ${saved.job.location}` : ''}
                </div>
              </div>
              <button
                onClick={() => unsaveJob(saved.job.id)}
                disabled={isUnsaving}
                className="text-xs px-3 py-1 border border-[var(--color-border)] hover:bg-[var(--color-bg-secondary)] transition-colors"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
