import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';

const NAV = [
  { to: '/',            label: 'Today'       },
  { to: '/jobs',        label: 'Jobs'        },
  { to: '/search',      label: 'Search'      },
  { to: '/saved',       label: 'Saved'       },
  { to: '/applications',label: 'Applied'     },
  { to: '/system',      label: 'System'      },
  { to: '/settings',    label: 'Settings'    },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  const { user, isFetching, isError, logout, isLoggingOut } = useAuth();

  // Only show initializing on the very first fetch before we know if they are logged in or not
  const isInitialLoading = isFetching && !isError && user === undefined;

  if (isInitialLoading) {
    return (
      <div style={{ minHeight: '100vh', background: 'var(--cream)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 17, color: 'var(--ink-muted)' }}>
        Initializing
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--cream)' }}>
      <header style={{
        background: 'var(--ink)',
        display: 'flex',
        alignItems: 'stretch',
        height: 52,
        flexShrink: 0,
      }}>
        {/* Wordmark */}
        <Link to="/" style={{
          display: 'flex', alignItems: 'center',
          padding: '0 24px',
          fontSize: 15, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase',
          color: 'var(--cream)', textDecoration: 'none',
          borderRight: '1px solid rgba(255,255,255,0.1)',
        }}>
          JobPilot
        </Link>

        {/* Nav */}
        <nav style={{ display: 'flex', alignItems: 'stretch', flex: 1, overflowX: 'auto' }}>
          {NAV.map(({ to, label }) => {
            const active = pathname === to;
            return (
              <Link key={to} to={to} style={{
                display: 'flex', alignItems: 'center',
                padding: '0 20px',
                fontSize: 16, fontWeight: active ? 600 : 400,
                color: active ? '#fff' : 'rgba(255,255,255,0.55)',
                textDecoration: 'none',
                background: active ? 'rgba(255,255,255,0.1)' : 'transparent',
                borderRight: '1px solid rgba(255,255,255,0.06)',
                transition: 'color 0.15s, background 0.15s',
                whiteSpace: 'nowrap',
              }}>
                {label}
              </Link>
            );
          })}
        </nav>

        {/* Auth */}
        <div style={{ display: 'flex', alignItems: 'center', padding: '0 20px', borderLeft: '1px solid rgba(255,255,255,0.1)', gap: 16, flexShrink: 0 }}>
          {user && (
            <span style={{ fontSize: 14, color: 'rgba(255,255,255,0.4)', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {user.email}
            </span>
          )}
          {user ? (
            <button onClick={() => logout()} disabled={isLoggingOut} style={{
              fontSize: 15, fontWeight: 500, color: 'rgba(255,255,255,0.7)',
              background: 'none', border: 'none', cursor: 'pointer', padding: 0,
              transition: 'color 0.15s',
            }}>
              {isLoggingOut ? 'Signing out…' : 'Sign out'}
            </button>
          ) : (
            <Link to="/signin" style={{ fontSize: 15, fontWeight: 500, color: 'rgba(255,255,255,0.7)', textDecoration: 'none' }}>
              Sign in
            </Link>
          )}
        </div>
      </header>

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        {children}
      </main>
    </div>
  );
}
