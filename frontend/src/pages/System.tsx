import { useSystemStatus } from '@/hooks/useSystemStatus';
import { formatDistanceToNow } from 'date-fns';

export function System() {
  const { data: status, isLoading, isError } = useSystemStatus();

  if (isLoading) return <div className="max-w-4xl mx-auto animate-pulse h-32 bg-[var(--color-border)]"></div>;
  if (isError || !status) return <div className="max-w-4xl mx-auto text-[var(--color-signal-error)]">Failed to connect to engine telemetry.</div>;

  const isFailed = status.latest_execution_status === 'failed';
  const isSucceeded = status.latest_execution_status === 'succeeded';

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="font-serif text-4xl mb-8">System Telemetry</h1>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="p-6 border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
          <div className="text-xs text-[var(--color-text-secondary)] uppercase tracking-widest mb-4">Latest Execution</div>
          <div className="flex items-center gap-3 mb-2">
            <div className={`w-2.5 h-2.5 rounded-full ${
              isSucceeded ? 'bg-[var(--color-signal-success)]' : isFailed ? 'bg-[var(--color-signal-error)]' : 'bg-[var(--color-border)]'
            }`}></div>
            <span className="font-medium text-lg uppercase tracking-wide">
              {status.latest_execution_status || 'None'}
            </span>
          </div>
          <p className="text-xs text-[var(--color-text-secondary)] mt-4">
            Outcome of the most recent search cycle.
          </p>
        </div>

        <div className="p-6 border border-[var(--color-border)] bg-white/40">
          <div className="text-xs text-[var(--color-text-secondary)] uppercase tracking-widest mb-4">Last Success</div>
          <div className="font-medium text-lg mb-2">
            {status.last_sync ? formatDistanceToNow(new Date(status.last_sync), { addSuffix: true }) : 'Never'}
          </div>
          <p className="text-xs text-[var(--color-text-secondary)] mt-4">
            Authoritative completion timestamp from telemetry.
          </p>
        </div>

        <div className="p-6 border border-[var(--color-border)] bg-white/40">
          <div className="text-xs text-[var(--color-text-secondary)] uppercase tracking-widest mb-4">Execution History</div>
          <div className="flex items-center gap-3 mb-2">
            <div className={`w-2.5 h-2.5 rounded-full ${status.engine_active ? 'bg-[var(--color-signal-success)]' : 'bg-[var(--color-signal-error)]'}`}></div>
            <span className="font-medium text-lg uppercase tracking-wide">
              {status.engine_active ? 'Available' : 'None Recorded'}
            </span>
          </div>
          <p className="text-xs text-[var(--color-text-secondary)] mt-4">
            Telemetry recorded in execution database.
          </p>
        </div>

        <div className="p-6 border border-[var(--color-border)] bg-white/40 md:col-span-3 flex justify-between items-center">
          <div>
            <div className="text-xs text-[var(--color-text-secondary)] uppercase tracking-widest mb-1">Total Jobs Processed</div>
            <div className="font-serif text-4xl">{status.total_processed.toLocaleString()}</div>
          </div>
          <div className="text-right text-xs text-[var(--color-text-secondary)] max-w-xs">
            Lifetime count of raw opportunities ingested and evaluated against user taxonomy.
          </div>
        </div>
      </div>
    </div>
  );
}
