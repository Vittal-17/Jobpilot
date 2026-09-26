import { formatDistanceToNow, parseISO } from 'date-fns';
import { useSystemStatus } from '@/hooks/useSystemStatus';
import { Pipeline } from '@/components/Pipeline';

export function System() {
  const { data: status, isLoading, isError } = useSystemStatus();

  if (isLoading) return <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--ink-muted)' }}>Connecting…</div>;
  if (isError || !status) return <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--vermillion)' }}>Engine unreachable</div>;

  const engineStatus = status.latest_execution_status;
  const isFailed = engineStatus === 'failed';
  const isActive = status.engine_active && !isFailed;
  const statusColor = isFailed ? 'var(--vermillion)' : isActive ? 'var(--mint)' : engineStatus === 'succeeded' ? 'var(--acid)' : 'var(--stone-dark)';
  const statusLabel = isFailed ? 'Error' : isActive ? 'Running' : engineStatus === 'succeeded' ? 'Ready' : 'Idle';

  return (
    <div style={{ minHeight: 'calc(100vh - 52px)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '24px 32px', borderBottom: '1px solid var(--stone)', background: 'var(--sand)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h1 style={{ margin: 0, fontSize: 32, fontWeight: 800, letterSpacing: '-0.02em' }}>System State</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 14, height: 14, borderRadius: '50%', background: statusColor }} />
          <span style={{ fontSize: 20, fontWeight: 700, color: statusColor }}>{statusLabel}</span>
        </div>
      </div>

      <Pipeline
        totalProcessed={status.total_processed}
        totalMatched={null}
        totalSurfaced={null}
        engineStatus={engineStatus}
        isActive={isActive}
      />

      {/* Stats */}
      <div style={{ display: 'flex', flexWrap: 'wrap', borderBottom: '1px solid var(--stone)' }}>
        {[
          { label: 'Total Processed', value: status.total_processed.toLocaleString(), color: 'var(--cobalt)', large: true },
          { label: 'Last Successful Sync', value: status.last_sync ? `${formatDistanceToNow(parseISO(status.last_sync))} ago` : 'Never', color: 'var(--ink)', large: false },
          { label: 'Latest Run', value: engineStatus ?? 'unknown', color: statusColor, large: false },
          { label: 'Engine Active', value: status.engine_active ? 'Yes' : 'No', color: isActive ? 'var(--mint)' : 'var(--stone-dark)', large: false },
        ].map(({ label, value, color, large }) => (
          <div key={label} style={{
            flex: large ? '2 1 300px' : '1 1 180px',
            padding: '28px 28px',
            borderRight: '1px solid var(--stone)',
            background: large ? 'var(--cobalt)' : 'var(--cream)',
          }}>
            <div style={{ fontSize: 14, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: large ? 'rgba(255,255,255,0.6)' : 'var(--ink-muted)', marginBottom: 10 }}>
              {label}
            </div>
            <div style={{
              fontFamily: large ? 'var(--font-mono)' : 'var(--font-sans)',
              fontSize: large ? 'clamp(40px, 5vw, 72px)' : 28,
              fontWeight: 800, lineHeight: 1,
              color: large ? '#fff' : color,
              letterSpacing: large ? '-0.04em' : '-0.01em',
            }}>
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* Pipeline explainer */}
      <div style={{ padding: '36px 32px' }}>
        <h2 style={{ margin: '0 0 24px', fontSize: 22, fontWeight: 700, color: 'var(--ink)' }}>
          How the pipeline works
        </h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 600 }}>
          {[
            ['DISCOVER',  'var(--cobalt)',    'Raw job postings are pulled from all configured sources continuously.'],
            ['NORMALIZE', 'var(--violet)',    'Each posting is parsed into a canonical schema: title, company, location, type.'],
            ['DEDUPE',    'var(--electric)',  'Duplicate postings across sources are identified and collapsed.'],
            ['ENRICH',    'var(--mint)',      'Additional metadata is resolved: salary, employment type, remote flag.'],
            ['MATCH',     'var(--amber)',     'Each job is scored against your active profile vectors and preferences.'],
            ['SURFACE',   'var(--vermillion)','High-confidence matches are queued for operator review on Today.'],
          ].map(([stage, color, desc]) => (
            <div key={stage} style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
              <div style={{
                width: 100, flexShrink: 0, fontSize: 13, fontWeight: 800,
                textTransform: 'uppercase', letterSpacing: '0.08em',
                color: color, paddingTop: 2,
              }}>
                {stage}
              </div>
              <div style={{ fontSize: 16, color: 'var(--ink-soft)', lineHeight: 1.6 }}>{desc}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
