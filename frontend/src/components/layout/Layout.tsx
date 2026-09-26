import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useMagnetic } from '@/lib/anim';

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
  // Wordmark drifts toward the cursor; sits dead-centre (x/y = 0) at rest.
  const brandRef = useMagnetic<HTMLAnchorElement>(0.4);

  // Only show initializing on the very first fetch before we know if they are logged in or not
  const isInitialLoading = isFetching && !isError && user === undefined;

  if (isInitialLoading) {
    return (
      <div className="mast-init"><i />Initializing</div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--cream)' }}>
      <header className="mast">
        <Link to="/" className="mast-brand" ref={brandRef}>
          <b>JobPilot</b>
          <span>The Operator&rsquo;s Log</span>
        </Link>

        <nav className="mast-nav">
          {NAV.map(({ to, label }) => (
            <Link key={to} to={to} className={`mast-tab ${pathname === to ? 'mast-tab--on' : ''}`}>
              {label}
            </Link>
          ))}
        </nav>

        <div className="mast-meta">
          {user && <span className="mast-user">{user.email}</span>}
          {user ? (
            <button className="mast-act" onClick={() => logout()} disabled={isLoggingOut}>
              {isLoggingOut ? 'Signing out…' : 'Sign out'}
            </button>
          ) : (
            <Link to="/signin" className="mast-act">Sign in</Link>
          )}
        </div>
      </header>

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        {children}
      </main>
    </div>
  );
}
