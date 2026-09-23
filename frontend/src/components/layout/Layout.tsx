import { useEffect } from 'react';
import { ReactLenis } from 'lenis/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { apiClient } from '@/api/client';

gsap.registerPlugin(ScrollTrigger);

export function Layout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    // Reset ScrollTrigger on route changes if needed
    ScrollTrigger.refresh();
  }, []);

  return (
    <ReactLenis root>
      <div className="min-h-screen flex flex-col">
        <header className="border-b border-[var(--color-border)] py-4 px-6 md:px-12 flex justify-between items-center">
          <div className="font-serif text-xl font-medium tracking-tight">JobPilot.</div>
          <nav className="flex gap-6 text-sm font-sans text-[var(--color-text-secondary)] items-center">
            <a href="/" className="hover:text-[var(--color-text-primary)] transition-colors">Today</a>
            <a href="/jobs" className="hover:text-[var(--color-text-primary)] transition-colors">Feed</a>
            <a href="/search" className="hover:text-[var(--color-text-primary)] transition-colors">Search</a>
            <a href="/system" className="hover:text-[var(--color-text-primary)] transition-colors">System</a>
            <a href="/settings" className="hover:text-[var(--color-text-primary)] transition-colors">Settings</a>
            <button
              onClick={async () => {
                await apiClient.post('/v1/auth/logout');
                window.location.href = '/login';
              }}
              className="ml-4 px-3 py-1 border border-[var(--color-border)] hover:border-[var(--color-text-primary)] transition-colors">
              Logout
            </button>
          </nav>
        </header>
        <main className="flex-1 px-6 md:px-12 py-12">
          {children}
        </main>
        <footer className="border-t border-[var(--color-border)] py-6 px-6 md:px-12 text-xs font-sans text-[var(--color-text-secondary)] flex justify-between">
          <span>JobPilot Engine</span>
          <span>Status: Autonomous</span>
        </footer>
      </div>
    </ReactLenis>
  );
}
