import { useState, useRef } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { apiClient } from '@/api/client';

const reduced = () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Printed folio date — real, set once (no ticking clock; this is a page, not a terminal)
const ISSUE_DATE = new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase();

export function Login() {
  const [email, setEmail]       = useState('');
  const [password, setPassword] = useState('');
  const [error, setError]       = useState('');
  const [loading, setLoading]   = useState(false);
  const navigate = useNavigate();
  const rootRef  = useRef<HTMLDivElement>(null);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await apiClient.post('/v1/auth/login', { email, password });
      navigate('/');
    } catch {
      setError('Those credentials were not recognised.');
      if (!reduced()) {
        gsap.fromTo('.leaf-form', { x: -9 }, { x: 0, duration: 0.5, ease: 'elastic.out(1, 0.4)' });
      }
    } finally {
      setLoading(false);
    }
  };

  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add('(prefers-reduced-motion: no-preference)', () => {
      gsap.from('.leaf-index', { autoAlpha: 0, duration: 1, ease: 'power2.out' });
      gsap.from('.leaf-kicker, .leaf-head, .leaf-deck, .leaf-meta', { y: 22, autoAlpha: 0, duration: 0.7, stagger: 0.1, ease: 'power3.out' });
      gsap.from('.leaf-form-tag, .leaf-field, .leaf-submit, .leaf-alt', { y: 16, autoAlpha: 0, duration: 0.6, stagger: 0.09, delay: 0.25, ease: 'power3.out' });
      // the wabi-sabi ring, drawn by an unhurried hand
      gsap.fromTo('.leaf-ring path', { strokeDasharray: 760, strokeDashoffset: 760 },
        { strokeDashoffset: 0, duration: 1.1, delay: 0.7, ease: 'power2.inOut' });
    });
    return () => mm.revert();
  }, { scope: rootRef });
  return (
    <div className="leaf" ref={rootRef}>
      {/* top folio */}
      <div className="leaf-folio leaf-folio--top">
        <div className="leaf-folio-seg">
          <b>JOBPILOT</b>
          <span className="leaf-hide">— The Operator&rsquo;s Log</span>
        </div>
        <div className="leaf-folio-seg">
          <span>Issue 01</span>
          <span className="leaf-hide">· {ISSUE_DATE}</span>
        </div>
      </div>

      <div className="leaf-grid">
        {/* masthead */}
        <div className="leaf-master">
          <span className="leaf-index" aria-hidden>01</span>
          <div className="leaf-kicker">Authentication</div>
          <h1 className="leaf-head">
            Return to the{' '}
            <em>
              engine
              <svg className="leaf-ring" viewBox="0 0 320 120" preserveAspectRatio="none" aria-hidden>
                <path d="M18 66 C 30 26, 210 12, 292 34 C 322 42, 312 92, 214 104 C 96 118, 20 100, 12 62" />
              </svg>
            </em>.
          </h1>
          <p className="leaf-deck">
            Your shortlists, applications, and profile vectors live behind this page. The public instruments — Today, Jobs, System, Search — stay open to everyone, no key required.
          </p>
          <div className="leaf-meta">
            <span><i style={{ display: 'inline-block', background: 'var(--cobalt)' }} /> Saved</span>
            <span><i style={{ display: 'inline-block', background: 'var(--violet)' }} /> Applications</span>
            <span><i style={{ display: 'inline-block', background: 'var(--amber)' }} /> Profile vectors</span>
          </div>
        </div>
        {/* sign-in leaf */}
        <div className="leaf-form-wrap">
          <div className="leaf-form-tag">
            <span>Credentials</span>
            <span>Sign in</span>
          </div>
          <form className="leaf-form" onSubmit={handleLogin} noValidate>
            {error && <div className="leaf-err" role="alert"><span aria-hidden>†</span>{error}</div>}
            <div className="leaf-field">
              <label htmlFor="leaf-email">Email</label>
              <input id="leaf-email" className="leaf-input" type="email" value={email} required autoComplete="email" placeholder="operator@domain.com" onChange={e => setEmail(e.target.value)} />
            </div>
            <div className="leaf-field">
              <label htmlFor="leaf-pass">Password</label>
              <input id="leaf-pass" className="leaf-input" type="password" value={password} required autoComplete="current-password" placeholder="Your passphrase" onChange={e => setPassword(e.target.value)} />
            </div>
            <button type="submit" className="leaf-submit" disabled={loading}>
              {loading ? 'Signing in…' : <>Sign in <span aria-hidden>→</span></>}
            </button>
          </form>
          <p className="leaf-alt">
            No key needed to look around. <Link to="/">Browse JobPilot →</Link>
          </p>
        </div>
      </div>

      {/* bottom folio */}
      <div className="leaf-folio leaf-folio--bottom">
        <div className="leaf-folio-seg"><span>001 — Sign in</span></div>
        <div className="leaf-folio-seg"><Link to="/">Return to the public pages →</Link></div>
      </div>
    </div>
  );

}
