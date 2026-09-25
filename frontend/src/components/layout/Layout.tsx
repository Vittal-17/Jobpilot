import { useEffect } from 'react';
import { ReactLenis } from 'lenis/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';

gsap.registerPlugin(ScrollTrigger);

export function Layout({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  const { user, isLoading, logout, isLoggingOut } = useAuth();

  useEffect(() => {
    ScrollTrigger.refresh();
  }, [pathname]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-sm text-[var(--color-text-secondary)]">
        Bootstrapping system...
      </div>
    );
  }

  // If useAuth fails to fetch user (401), the interceptor will redirect to /signin.
  // But we render children anyway; they will be protected by the same mechanism or interceptor.

  return (
    <ReactLenis root>
      <div className="noise-overlay" aria-hidden="true" />
      <div className="min-h-screen flex flex-col relative z-10 selection:bg-[var(--color-text-primary)] selection:text-[var(--color-bg-primary)]">

        {/* Header - 12 col grid */}
        <header className="border-b border-[var(--color-border-strong)] swiss-grid items-center px-4 md:px-8">

          <div className="col-span-12 md:col-span-2 py-4 border-b md:border-b-0 md:border-r border-[var(--color-border)] flex items-center">
            <Link to="/" className="font-serif text-2xl tracking-tight leading-none text-[var(--color-text-primary)]">JobPilot.</Link>
          </div>

          <nav className="col-span-12 md:col-span-8 py-3 md:py-0 flex flex-wrap md:flex-nowrap gap-x-6 gap-y-2 text-xs font-sans uppercase tracking-widest text-[var(--color-text-secondary)] items-center md:px-8">
            <Link to="/" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/' ? 'text-[var(--color-text-primary)] font-medium' : ''}`}>Today</Link>
            <Link to="/jobs" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/jobs' ? 'text-[var(--color-text-primary)] font-medium' : ''}`}>Feed</Link>
            <Link to="/saved" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/saved' ? 'text-[var(--color-text-primary)] font-medium' : ''}`}>Saved</Link>
            <Link to="/applications" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/applications' ? 'text-[var(--color-text-primary)] font-medium' : ''}`}>Applications</Link>
            <Link to="/search" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/search' ? 'text-[var(--color-text-primary)] font-medium' : ''}`}>Search</Link>
            <Link to="/system" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/system' ? 'text-[var(--color-text-primary)] font-medium' : ''}`}>System</Link>
            <Link to="/settings" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/settings' ? 'text-[var(--color-text-primary)] font-medium' : ''}`}>Settings</Link>
          </nav>

          <div className="hidden md:flex col-span-12 md:col-span-2 py-4 border-l border-[var(--color-border)] justify-end items-center pl-8">
            <button
              onClick={() => logout()}
              disabled={isLoggingOut}
              className="text-xs uppercase tracking-widest text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors disabled:opacity-50">
              {isLoggingOut ? 'TERMINATING...' : 'LOGOUT'}
            </button>
          </div>
        </header>

        <main className="flex-1 px-4 md:px-8 py-8 md:py-16">
          {children}
        </main>

        {/* Footer - Dense status bar */}
        <footer className="border-t border-[var(--color-border-strong)] bg-[var(--color-bg-secondary)] px-4 md:px-8 py-3 text-[10px] uppercase tracking-widest font-sans text-[var(--color-text-secondary)] swiss-grid">
          <div className="col-span-12 md:col-span-4 flex items-center">
            {user?.email || 'SYSTEM UNINITIALIZED'}
          </div>
          <div className="col-span-12 md:col-span-4 flex items-center md:justify-center mt-2 md:mt-0">
            INTELLIGENCE WORKSTATION v1.0
          </div>
          <div className="col-span-12 md:col-span-4 flex items-center md:justify-end mt-2 md:mt-0">
            <span className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 bg-[var(--color-signal-success)] rounded-none inline-block animate-pulse"></span>
              AUTONOMOUS
            </span>
          </div>
        </footer>
      </div>
    </ReactLenis>
  );
}
