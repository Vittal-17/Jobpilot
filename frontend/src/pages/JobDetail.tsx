import { useParams, Link, useNavigate } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useJob } from '@/hooks/useJob';
import { useSaveJob } from '@/hooks/useSavedJobs';
import { useApplyJob } from '@/hooks/useApplications';

export function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const jobId = id ? parseInt(id, 10) : undefined;
  const navigate = useNavigate();
  const { user } = useAuth();

  const { data: job, isLoading, isError } = useJob(jobId);
  const { saveJob, isSaving } = useSaveJob();
  const { applyJob, isApplying } = useApplyJob();

  const handleSave = () => {
    if (!user) return navigate('/signin');
    if (jobId) saveJob(jobId);
  };

  const handleApply = () => {
    if (!user) return navigate('/signin');
    if (jobId) applyJob(jobId);
  };

  if (isLoading) {
    return (
      <div className="max-w-3xl mx-auto animate-pulse space-y-4">
        <div className="h-8 bg-[var(--color-border)] w-1/2"></div>
        <div className="h-4 bg-[var(--color-border)] w-1/4"></div>
        <div className="h-32 bg-[var(--color-border)] w-full mt-8"></div>
      </div>
    );
  }

  if (isError || !job) {
    return (
      <div className="max-w-3xl mx-auto p-4 border border-[var(--color-signal-error)] bg-[var(--color-signal-error)]/10 text-sm">
        Failed to load job details.
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto">
      <Link to="/" className="text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] mb-8 inline-block">
        ← Back
      </Link>

      <header className="mb-12">
        <div className="flex gap-4 text-xs uppercase tracking-widest text-[var(--color-text-secondary)] mb-4">
          <span>{job.source}</span>
          {job.published_at && <span>{new Date(job.published_at).toLocaleDateString()}</span>}
        </div>
        <h1 className="font-serif text-4xl md:text-5xl mb-4 leading-tight">{job.title}</h1>
        <div className="text-lg font-sans">{job.company} {job.location ? `— ${job.location}` : ''}</div>

        <div className="flex gap-4 mt-6">
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="px-4 py-2 bg-[var(--color-bg-secondary)] border border-[var(--color-border)] hover:border-[var(--color-text-primary)] text-sm transition-colors disabled:opacity-50"
          >
            {isSaving ? 'Saving...' : 'Save Job'}
          </button>
          <button
            onClick={handleApply}
            disabled={isApplying}
            className="px-4 py-2 bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] hover:bg-[var(--color-text-secondary)] text-sm transition-colors disabled:opacity-50"
          >
            {isApplying ? 'Applying...' : 'Mark as Applied'}
          </button>
          {job.url && (
            <a href={job.url} target="_blank" rel="noreferrer" className="px-4 py-2 border border-[var(--color-border)] hover:border-[var(--color-text-primary)] text-sm transition-colors">
              External Link ↗
            </a>
          )}
        </div>
      </header>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-12 p-6 border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
        <div>
          <div className="text-xs text-[var(--color-text-secondary)] mb-1">Remote</div>
          <div className="text-sm">{job.remote ? 'Yes' : 'No'}</div>
        </div>
        <div>
          <div className="text-xs text-[var(--color-text-secondary)] mb-1">Type</div>
          <div className="text-sm">{job.employment_type || '--'}</div>
        </div>
        <div>
          <div className="text-xs text-[var(--color-text-secondary)] mb-1">Salary Min</div>
          <div className="text-sm">{job.salary_min ? `${job.salary_min} ${job.currency || ''}` : '--'}</div>
        </div>
        <div>
          <div className="text-xs text-[var(--color-text-secondary)] mb-1">Salary Max</div>
          <div className="text-sm">{job.salary_max ? `${job.salary_max} ${job.currency || ''}` : '--'}</div>
        </div>
      </div>

      <article className="prose prose-neutral max-w-none text-sm leading-relaxed whitespace-pre-wrap">
        {job.description || <span className="italic text-[var(--color-text-secondary)]">No description available.</span>}
      </article>
    </div>
  );
}
