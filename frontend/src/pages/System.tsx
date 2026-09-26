import { formatDistanceToNow, parseISO } from 'date-fns';
import { useSystemStatus } from '@/hooks/useSystemStatus';
import { Pipeline } from '@/components/Pipeline';
import { PageState } from '@/components/PageState';
import { SectionHead } from '@/components/SectionHead';
import { Mark } from '@/components/Mark';

export function System() {
  const { data: status, isLoading, isError } = useSystemStatus();

  const engineStatus = status?.latest_execution_status ?? null;
  const isFailed = engineStatus === 'failed';
  const isActive = !!status?.engine_active && !isFailed;

  const statusColor = isError ? 'var(--vermillion)'
    : isLoading ? 'var(--stone-dark)'
    : isFailed ? 'var(--vermillion)'
    : isActive ? 'var(--mint)'
    : engineStatus === 'succeeded' ? 'var(--acid)'
    : 'var(--stone-dark)';
  const statusLabel = isError ? 'Unreachable'
    : isLoading ? 'Connecting'
    : isFailed ? 'Error'
    : isActive ? 'Running'
    : engineStatus === 'succeeded' ? 'Ready'
    : 'Idle';

  const telemetryLabel: React.CSSProperties = {
    fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700,
    textTransform: 'uppercase', letterSpacing: '0.12em', marginBottom: 12,
  };

  const body = (() => {
    if (isLoading) {
      return (
        <PageState
          eyebrow="Observability"
          title="Connecting to the engine…"
          body="Reading live pipeline state and telemetry from the recommendation engine."
          motif={[
            { label: 'DISCOVER', color: 'var(--cobalt)' },
            { label: 'NORMALIZE', color: 'var(--violet)' },
            { label: 'ENRICH', color: 'var(--mint)' },
            { label: 'MATCH', color: 'var(--amber)' },
            { label: 'SURFACE', color: 'var(--vermillion)' },
          ]}
        />
      );
    }
    if (isError || !status) {
      return (
        <PageState
          tone="error"
          eyebrow="Observability"
          title="The engine is not responding."
          body="Telemetry could not be read from the recommendation engine. This does not mean the pipeline has stopped — only that the status endpoint is unreachable right now. Live state will reappear the moment contact is restored."
          motif={[
            { label: 'DISCOVER', color: 'var(--stone-dark)' },
            { label: 'NORMALIZE', color: 'var(--stone-dark)' },
            { label: 'ENRICH', color: 'var(--stone-dark)' },
            { label: 'MATCH', color: 'var(--stone-dark)' },
            { label: 'SURFACE', color: 'var(--stone-dark)' },
          ]}
        />
      );
    }

    return (
      <>
        <Pipeline
          totalProcessed={status.total_processed}
          totalMatched={null}
          totalSurfaced={null}
          engineStatus={engineStatus}
          isActive={isActive}
        />

        <div style={{ display: 'flex', flexWrap: 'wrap', flex: 1, borderTop: '1px solid var(--stone)' }}>
          {/* Telemetry */}
          <div style={{ flex: '1 1 600px', display: 'flex', flexWrap: 'wrap', alignContent: 'flex-start' }}>
            {[
              { label: 'Total Processed', value: status.total_processed.toLocaleString(), color: 'var(--cobalt)', large: true },
              { label: 'Last Successful Sync', value: status.last_sync ? `${formatDistanceToNow(parseISO(status.last_sync))} ago` : 'Never', color: 'var(--ink)', large: false },
              { label: 'Latest Run', value: engineStatus ?? 'unknown', color: statusColor, large: false },
              { label: 'Engine Active', value: status.engine_active ? 'Yes' : 'No', color: isActive ? 'var(--mint)' : 'var(--stone-dark)', large: false },
            ].map(({ label, value, color, large }) => (
              <div key={label} style={{
                flex: large ? '1 1 100%' : '1 1 200px',
                padding: '36px 32px',
                borderRight: '1px solid var(--stone)',
                borderBottom: '1px solid var(--stone)',
                background: large ? 'var(--cobalt)' : 'var(--cream)',
              }}>
                <div style={{ ...telemetryLabel, color: large ? 'rgba(255,255,255,0.6)' : 'var(--ink-muted)' }}>
                  {label}
                </div>
                <div style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: large ? 'clamp(40px, 5vw, 72px)' : 28,
                  fontWeight: 800, lineHeight: 1,
                  color: large ? '#fff' : color,
                  letterSpacing: '-0.04em',
                }}>
                  {value}
                </div>
              </div>
            ))}
          </div>

          {/* Pipeline explainer */}
          <div style={{ flex: '1 1 400px', padding: '36px 32px', background: 'var(--sand)', borderBottom: '1px solid var(--stone)' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--cobalt)', marginBottom: 24 }}>
              How the pipeline works
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {[
                ['DISCOVER',  'var(--cobalt)',    'Raw job postings are pulled from all configured sources continuously.'],
                ['NORMALIZE', 'var(--violet)',    'Each posting is parsed into a canonical schema: title, company, location, type.'],
                ['DEDUPE',    'var(--electric)',  'Duplicate postings across sources are identified and collapsed.'],
                ['ENRICH',    'var(--mint)',      'Additional metadata is resolved: salary, employment type, remote flag.'],
                ['MATCH',     'var(--amber)',     'Each job is scored against your active profile vectors and preferences.'],
                ['SURFACE',   'var(--vermillion)','High-confidence matches are queued for operator review on Today.'],
              ].map(([stage, color, desc]) => (
                <div key={stage} style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
                  <div style={{ width: 100, flexShrink: 0, fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: color, paddingTop: 2 }}>
                    {stage}
                  </div>
                  <div style={{ fontSize: 16, color: 'var(--ink-soft)', lineHeight: 1.6 }}>{desc}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </>
    );
  })();

  return (
    <div className="page">
      <SectionHead
        index="06"
        kicker="Observability"
        title={<>The engine, <em>live<Mark variant="pulse" /></em>.</>}
        deck="Live observability for the JobPilot recommendation engine — pipeline throughput and telemetry, read straight from the source."
        aside={<div className="sec-status"><i style={{ background: statusColor }} />{statusLabel}</div>}
      />
      {body}
    </div>
  );
}
