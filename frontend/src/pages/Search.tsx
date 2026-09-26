import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useSearches } from '@/hooks/useSearches';
import { useAuth } from '@/hooks/useAuth';

export function Search() {
  const { user } = useAuth();
  const { searches, isLoading, isError, createSearch, deleteSearch, updateSearch, isCreating } = useSearches(1, 50, !!user);
  const [query, setQuery]       = useState('');
  const [location, setLocation] = useState('');
  const [remoteOnly, setRemoteOnly] = useState(false);

  if (isLoading) return <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--ink-muted)' }}>Loading…</div>;
  if (isError)   return <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--vermillion)' }}>Failed to load search configuration</div>;

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() && !location.trim()) return;
    await createSearch({ query, location, remote_only: remoteOnly, enabled: true });
    setQuery(''); setLocation(''); setRemoteOnly(false);
  };

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '12px 14px', fontSize: 16,
    border: '1px solid var(--stone)', background: 'var(--cream)',
    color: 'var(--ink)', fontFamily: 'inherit', outline: 'none',
    transition: 'border-color 0.15s',
  };

  return (
    <div style={{ display: 'flex', minHeight: 'calc(100vh - 52px)' }}>

      {/* Left: Search list */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--stone)' }}>
        <div style={{
          padding: '24px 32px 20px', borderBottom: '1px solid var(--stone)', background: 'var(--sand)',
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
        }}>
          <h1 style={{ margin: 0, fontSize: 32, fontWeight: 800, letterSpacing: '-0.02em' }}>Search Vectors</h1>
          <span style={{ fontSize: 17, color: 'var(--ink-muted)' }}>{searches?.items.length ?? 0} active</span>
        </div>

        {/* Column headers */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 100px 120px', borderBottom: '1px solid var(--stone)', background: 'var(--sand)' }}>
          {['Query / Location', 'Remote', user ? 'Controls' : 'Status'].map((h, i) => (
            <div key={h} style={{
              padding: '10px 20px', fontSize: 13, fontWeight: 700, textTransform: 'uppercase',
              letterSpacing: '0.06em', color: 'var(--ink-muted)',
              borderLeft: i > 0 ? '1px solid var(--stone)' : 'none',
            }}>
              {h}
            </div>
          ))}
        </div>

        {!searches?.items.length && (
          <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--ink-muted)' }}>
            No search vectors configured yet.
          </div>
        )}

        {searches?.items.map(s => (
          <div
            key={s.id}
            className="signal-entry"
            style={{ display: 'grid', gridTemplateColumns: '1fr 100px 120px' }}
          >
            <div style={{ padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 18, fontWeight: 600, color: s.enabled ? 'var(--ink)' : 'var(--stone-dark)' }}>
                {s.query || <em style={{ fontWeight: 400 }}>Any role</em>}
              </span>
              {s.location && (
                <span style={{ fontSize: 15, color: 'var(--ink-muted)' }}>{s.location}</span>
              )}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', padding: '18px 16px', borderLeft: '1px solid var(--stone)' }}>
              <span style={{ fontSize: 15, fontWeight: s.remote_only ? 700 : 400, color: s.remote_only ? 'var(--acid)' : 'var(--stone-dark)' }}>
                {s.remote_only ? 'Remote' : 'Any'}
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '18px 16px', borderLeft: '1px solid var(--stone)' }}>
              {user ? (
                <>
                  <button
                    onClick={() => updateSearch({ id: s.id, payload: { enabled: !s.enabled } })}
                    style={{
                      fontSize: 15, fontWeight: 600,
                      color: s.enabled ? 'var(--cobalt)' : 'var(--stone-dark)',
                      background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                    }}
                  >
                    {s.enabled ? 'Active' : 'Paused'}
                  </button>
                  <button
                    onClick={() => deleteSearch(s.id)}
                    style={{
                      fontSize: 14, fontWeight: 600, color: 'var(--vermillion)',
                      background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                    }}
                  >
                    Remove
                  </button>
                </>
              ) : (
                <span style={{ fontSize: 15, fontWeight: 600, color: s.enabled ? 'var(--cobalt)' : 'var(--stone-dark)' }}>
                  {s.enabled ? 'Active' : 'Paused'}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Right: Add form or auth prompt */}
      <div style={{ width: 320, flexShrink: 0, background: 'var(--sand)', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '24px 24px 20px', borderBottom: '1px solid var(--stone)' }}>
          <h2 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: '-0.01em' }}>
            {user ? 'Add Vector' : 'Sign In to Configure'}
          </h2>
        </div>

        {user ? (
          <form onSubmit={handleCreate} style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: 20 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <label style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                Keywords
              </label>
              <input
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="e.g. Staff Engineer"
                style={inputStyle}
                onFocus={e => (e.target.style.borderColor = 'var(--cobalt)')}
                onBlur={e => (e.target.style.borderColor = 'var(--stone)')}
              />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <label style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                Location
              </label>
              <input
                value={location}
                onChange={e => setLocation(e.target.value)}
                placeholder="e.g. Remote / London"
                style={inputStyle}
                onFocus={e => (e.target.style.borderColor = 'var(--cobalt)')}
                onBlur={e => (e.target.style.borderColor = 'var(--stone)')}
              />
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={remoteOnly}
                onChange={e => setRemoteOnly(e.target.checked)}
                style={{ width: 18, height: 18, accentColor: 'var(--cobalt)', cursor: 'pointer' }}
              />
              <span style={{ fontSize: 16, fontWeight: 500, color: 'var(--ink)' }}>Remote only</span>
            </label>
            <button
              type="submit"
              disabled={isCreating || (!query.trim() && !location.trim())}
              style={{
                padding: '14px', fontSize: 16, fontWeight: 700,
                background: 'var(--cobalt)', color: '#fff', border: 'none',
                cursor: isCreating || (!query.trim() && !location.trim()) ? 'not-allowed' : 'pointer',
                opacity: isCreating || (!query.trim() && !location.trim()) ? 0.5 : 1,
                marginTop: 4,
              }}
            >
              {isCreating ? 'Adding…' : 'Add search vector'}
            </button>
          </form>
        ) : (
          <div style={{ padding: '28px 24px', display: 'flex', flexDirection: 'column', gap: 16 }}>
            <p style={{ margin: 0, fontSize: 16, color: 'var(--ink-soft)', lineHeight: 1.65 }}>
              You're viewing the search taxonomy in read-only mode. Sign in to add, pause, or remove search vectors.
            </p>
            <Link to="/signin" style={{ fontSize: 16, fontWeight: 700, color: 'var(--cobalt)', textDecoration: 'none' }}>
              Sign in →
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
