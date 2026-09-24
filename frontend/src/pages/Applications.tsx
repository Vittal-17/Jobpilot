import { Link } from 'react-router-dom';
import { useApplications, useUpdateApplication } from '@/hooks/useApplications';
import type { ApplicationStatus } from '@/api/types';

const APPLICATION_STATUSES: readonly ApplicationStatus[] = [
  'applied',
  'interviewing',
  'offer',
  'rejected',
  'withdrawn',
] as const;

function isApplicationStatus(val: string): val is ApplicationStatus {
  return (APPLICATION_STATUSES as readonly string[]).includes(val);
}

export function Applications() {
  const { data: applications, isLoading, isError } = useApplications();
  const { updateApplication, isUpdating } = useUpdateApplication();

  if (isLoading) {
    return <div className="max-w-4xl mx-auto animate-pulse h-32 bg-[var(--color-border)]"></div>;
  }

  if (isError) {
    return <div className="max-w-4xl mx-auto text-[var(--color-signal-error)]">Failed to load applications.</div>;
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="font-serif text-4xl mb-8">Applications</h1>

      {!applications?.items.length ? (
        <div className="text-sm text-[var(--color-text-secondary)] italic">No applications yet.</div>
      ) : (
        <div className="space-y-4">
          {applications.items.map((app) => (
            <div key={app.id} className="p-6 border border-[var(--color-border)] hover:border-[var(--color-text-primary)] transition-colors flex flex-col md:flex-row md:justify-between md:items-center gap-4 bg-white/40">
              <div>
                <Link to={`/jobs/${app.job.id}`} className="font-serif text-xl block mb-1 hover:underline">
                  {app.job.title}
                </Link>
                <div className="text-sm text-[var(--color-text-secondary)]">
                  {app.job.company} {app.job.location ? `— ${app.job.location}` : ''}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-[var(--color-text-secondary)]">Status:</span>
                <select
                  value={app.status}
                  onChange={(e) => {
                    const nextStatus = e.target.value;
                    if (isApplicationStatus(nextStatus)) {
                      updateApplication({ id: app.id, status: nextStatus });
                    }
                  }}
                  disabled={isUpdating}
                  className="text-sm p-1 border border-[var(--color-border)] bg-transparent focus:outline-none focus:border-[var(--color-text-primary)]"
                >
                  {APPLICATION_STATUSES.map(s => (
                    <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
                  ))}
                </select>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
