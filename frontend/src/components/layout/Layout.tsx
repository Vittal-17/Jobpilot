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

  // If useAuth fails to fetch user (401), the interceptor will redirect to /login.
  // But we render children anyway; they will be protected by the same mechanism or interceptor.

  return (
    <ReactLenis root>
      <div className="min-h-screen flex flex-col">
        <header className="border-b border-[var(--color-border)] py-4 px-6 md:px-12 flex justify-between items-center">
          <Link to="/" className="font-serif text-xl font-medium tracking-tight">JobPilot.</Link>
          <nav className="flex gap-6 text-sm font-sans text-[var(--color-text-secondary)] items-center">
            <Link to="/" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/' ? 'text-[var(--color-text-primary)]' : ''}`}>Today</Link>
            <Link to="/jobs" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/jobs' ? 'text-[var(--color-text-primary)]' : ''}`}>Feed</Link>
            <Link to="/saved" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/saved' ? 'text-[var(--color-text-primary)]' : ''}`}>Saved</Link>
            <Link to="/applications" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/applications' ? 'text-[var(--color-text-primary)]' : ''}`}>Applications</Link>
            <Link to="/search" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/search' ? 'text-[var(--color-text-primary)]' : ''}`}>Search</Link>
            <Link to="/system" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/system' ? 'text-[var(--color-text-primary)]' : ''}`}>System</Link>
            <Link to="/settings" className={`hover:text-[var(--color-text-primary)] transition-colors ${pathname === '/settings' ? 'text-[var(--color-text-primary)]' : ''}`}>Settings</Link>
            <button
              onClick={() => logout()}
              disabled={isLoggingOut}
              className="ml-4 px-3 py-1 border border-[var(--color-border)] hover:border-[var(--color-text-primary)] transition-colors disabled:opacity-50">
              {isLoggingOut ? '...' : 'Logout'}
            </button>
          </nav>
        </header>
        <main className="flex-1 px-6 md:px-12 py-12">
          {children}
        </main>
        <footer className="border-t border-[var(--color-border)] py-6 px-6 md:px-12 text-xs font-sans text-[var(--color-text-secondary)] flex justify-between">
          <span>{user?.email || 'JobPilot Engine'}</span>
          <span>Status: Autonomous</span>
        </footer>
      </div>
    </ReactLenis>
  );
}
