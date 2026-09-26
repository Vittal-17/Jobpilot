import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useSearches } from '@/hooks/useSearches';
import { useAuth } from '@/hooks/useAuth';
import { PageState } from '@/components/PageState';
import { SectionHead } from '@/components/SectionHead';
import { Mark } from '@/components/Mark';

const COLS = 'minmax(0, 1fr) 120px 140px';

export function Search() {
  const { user } = useAuth();
  const { searches, isLoading, isError, createSearch, deleteSearch, updateSearch, isCreating } = useSearches(1, 50, !!user);
  const [query, setQuery]       = useState('');
  const [location, setLocation] = useState('');
  const [remoteOnly, setRemoteOnly] = useState(false);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() && !location.trim()) return;
    await createSearch({ query, location, remote_only: remoteOnly, enabled: true });
    setQuery(''); setLocation(''); setRemoteOnly(false);
  };

  const items = searches?.items ?? [];
  const disabled = isCreating || (!query.trim() && !location.trim());

  const listBody = (() => {
    if (isLoading) {
      return <PageState eyebrow="Vectors" title="Loading search configuration…" body="Retrieving the vectors the discovery engine is currently scanning against." />;
    }
    if (isError) {
      return <PageState tone="error" eyebrow="Vectors" title="Couldn't load search vectors." body="Your search configuration is stored server-side and could not be retrieved. Try again once the connection settles." />;
    }
    if (items.length === 0) {
      return (
        <PageState
          eyebrow="Vectors"
          title={user ? 'No vectors configured yet.' : 'No public vectors to show.'}
          body={user
            ? 'A search vector tells the discovery engine what to scan for. Add one from the panel on the right and the stream begins filling against it.'
            : 'Search vectors shape what the engine surfaces. Sign in to configure your own and steer the stream.'}
          motif={[
            { label: 'KEYWORDS', color: 'var(--cobalt)' },
            { label: 'LOCATION', color: 'var(--violet)' },
            { label: 'REMOTE', color: 'var(--acid)' },
          ]}
        />
      );
    }
    return (
      <>
        <div className="grid-head" style={{ gridTemplateColumns: COLS }}>
          {['Query / Location', 'Remote', user ? 'Controls' : 'Status'].map(h => (
            <div key={h}>{h}</div>
          ))}
        </div>

        {items.map(s => (
          <div key={s.id} className="signal-entry" style={{ display: 'grid', gridTemplateColumns: COLS, cursor: 'default' }}>
            <div style={{ padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 4, borderLeft: `3px solid ${s.enabled ? 'var(--cobalt)' : 'var(--stone)'}` }}>
              <span style={{ fontSize: 18, fontWeight: 600, color: s.enabled ? 'var(--ink)' : 'var(--stone-dark)' }}>
                {s.query || <em style={{ fontWeight: 400 }}>Any role</em>}
              </span>
              {s.location && <span style={{ fontSize: 15, color: 'var(--ink-muted)' }}>{s.location}</span>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', padding: '18px 16px', borderLeft: '1px solid var(--stone)' }}>
              <span style={{ fontSize: 15, fontWeight: s.remote_only ? 700 : 400, color: s.remote_only ? 'var(--acid)' : 'var(--stone-dark)' }}>
                {s.remote_only ? 'Remote' : 'Any'}
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '18px 16px', borderLeft: '1px solid var(--stone)' }}>
              {user ? (
                <>
                  <button onClick={() => updateSearch({ id: s.id, payload: { enabled: !s.enabled } })} style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: s.enabled ? 'var(--cobalt)' : 'var(--stone-dark)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
                    {s.enabled ? 'Active' : 'Paused'}
                  </button>
                  <button onClick={() => deleteSearch(s.id)} style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--vermillion)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
                    Remove
                  </button>
                </>
              ) : (
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: s.enabled ? 'var(--cobalt)' : 'var(--stone-dark)' }}>
                  {s.enabled ? 'Active' : 'Paused'}
                </span>
              )}
            </div>
          </div>
        ))}
        <div className="hatch" style={{ flex: 1, borderTop: '1px solid var(--stone)', minHeight: 40 }} />
      </>
    );
  })();

  return (
    <div className="page">
      <SectionHead
        index="03"
        kicker="Vectors"
        title={<>What the engine <em>scans<Mark variant="arc" /></em> for.</>}
        deck="The parameters the discovery engine scans against — each vector widens or sharpens what surfaces in the stream."
        aside={<><span className="u">Configured</span><span>{items.length}</span></>}
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 360px', flex: 1, minHeight: 0 }}>
        {/* Vector list */}
        <div style={{ display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--stone)', minWidth: 0 }}>
          {listBody}
        </div>

        {/* Configuration rail */}
        <div style={{ background: 'var(--sand)', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <div style={{ padding: '22px 28px', borderBottom: '1px solid var(--stone)', display: 'flex', alignItems: 'baseline', gap: 12 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--cobalt)' }}>
              {user ? 'Add Vector' : 'Read Only'}
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--stone-dark)' }}>—</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--stone-dark)', letterSpacing: '0.06em' }}>{user ? 'define a scan' : 'sign in to edit'}</span>
          </div>

          {user ? (
            <form className="ed-form" onSubmit={handleCreate} style={{ padding: '28px' }}>
              <div className="ed-field">
                <label htmlFor="vec-query">Keywords</label>
                <input id="vec-query" className="ed-input" value={query} onChange={e => setQuery(e.target.value)} placeholder="e.g. Staff Engineer" />
              </div>
              <div className="ed-field">
                <label htmlFor="vec-loc">Location</label>
                <input id="vec-loc" className="ed-input" value={location} onChange={e => setLocation(e.target.value)} placeholder="e.g. Remote / London" />
              </div>
              <label className="ed-check">
                <input type="checkbox" checked={remoteOnly} onChange={e => setRemoteOnly(e.target.checked)} />
                <span>Remote only</span>
              </label>
              <button type="submit" className="ed-submit" disabled={disabled}>
                {isCreating ? 'Adding…' : <>Add vector <span aria-hidden>→</span></>}
              </button>
            </form>
          ) : (
            <div style={{ padding: '28px', display: 'flex', flexDirection: 'column', gap: 16 }}>
              <p style={{ margin: 0, fontSize: 16, color: 'var(--ink-soft)', lineHeight: 1.65 }}>
                You're viewing the search taxonomy in read-only mode. Sign in to add, pause, or remove search vectors.
              </p>
              <Link to="/signin" style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--cobalt)', textDecoration: 'none' }}>
                Sign in →
              </Link>
            </div>
          )}
          <div className="hatch" style={{ flex: 1, borderTop: '1px solid var(--stone)', minHeight: 40 }} />
        </div>
      </div>
    </div>
  );
}
