import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useMagnetic } from '@/lib/anim';
import { useState, useEffect, useRef } from 'react';

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
  const isInitialLoading = isFetching && !isError && user === undefined;
  // Wordmark drifts toward the cursor; sits dead-centre (x/y = 0) at rest.
  const brandRef = useMagnetic<HTMLAnchorElement>(0.4, [isInitialLoading]);

  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Close menu on route change
  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    setIsMenuOpen(false);
  }, [pathname]);
  /* oxlint-enable react/set-state-in-effect */

  // Close menu on escape or outside click
  useEffect(() => {
    if (!isMenuOpen) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setIsMenuOpen(false);
        buttonRef.current?.focus();
      }
    }

    function handleClickOutside(e: MouseEvent) {
      if (
        menuRef.current && !menuRef.current.contains(e.target as Node) &&
        buttonRef.current && !buttonRef.current.contains(e.target as Node)
      ) {
        setIsMenuOpen(false);
      }
    }

    document.addEventListener('keydown', handleKeyDown);
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isMenuOpen]);

  if (isInitialLoading) {
    return (
      <div className="mast-init"><i />Initializing</div>
    );
  }

  const activeLabel = NAV.find(n => n.to === pathname)?.label || 'JobPilot';

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--cream)', position: 'relative' }}>
      <header className="mast">
        <Link to="/" className="mast-brand" ref={brandRef}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <b>JobPilot</b>
            <span className="mast-hide">The Operator&rsquo;s Log</span>
          </div>
        </Link>

        {/* Mobile current section indicator */}
        <div className="mast-mobile-active">
          {activeLabel}
        </div>

        {/* Desktop Nav */}
        <nav className="mast-nav">
          {NAV.map(({ to, label }) => (
            <Link key={to} to={to} className={`mast-tab ${pathname === to ? 'mast-tab--on' : ''}`}>
              {label}
            </Link>
          ))}
          {user && (
            <a href="https://n8n.jobpilot.cfd" target="_blank" rel="noopener noreferrer" className="mast-tab mast-tab--ext">
              Automation ↗
            </a>
          )}
        </nav>

        {/* Desktop Meta / Mobile Toggle */}
        <div className="mast-meta">
          {user && <span className="mast-user">{user.email}</span>}
          {user ? (
            <button className="mast-act mast-act--logout" onClick={() => logout()} disabled={isLoggingOut}>
              {isLoggingOut ? 'Signing out…' : 'Sign out'}
            </button>
          ) : (
            <Link to="/signin" className="mast-act mast-act--login">Sign in</Link>
          )}

          <button
            ref={buttonRef}
            className="mast-menu-toggle"
            aria-expanded={isMenuOpen}
            aria-controls="mobile-menu"
            aria-label="Toggle navigation menu"
            onClick={() => setIsMenuOpen(!isMenuOpen)}
          >
            Menu
          </button>
        </div>
      </header>

      {/* Mobile Menu Dropdown */}
      {isMenuOpen && (
        <div
          id="mobile-menu"
          ref={menuRef}
          className="mast-mobile-menu"
        >
          {NAV.map(({ to, label }) => (
            <Link key={to} to={to} className={`mast-mobile-tab ${pathname === to ? 'mast-mobile-tab--on' : ''}`}>
              {label}
            </Link>
          ))}
          {user && (
            <a href="https://n8n.jobpilot.cfd" target="_blank" rel="noopener noreferrer" className="mast-mobile-tab">
              Automation ↗
            </a>
          )}
          <div className="mast-mobile-actions">
            {user ? (
              <button className="mast-act" onClick={() => logout()} disabled={isLoggingOut}>
                {isLoggingOut ? 'Signing out…' : 'Sign out'}
              </button>
            ) : (
              <Link to="/signin" className="mast-act">Sign in</Link>
            )}
          </div>
        </div>
      )}

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}>
        {children}
      </main>
    </div>
  );
}
