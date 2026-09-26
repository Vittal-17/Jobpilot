import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiClient } from '@/api/client';

export function Login() {
  const [email, setEmail]       = useState('');
  const [password, setPassword] = useState('');
  const [error, setError]       = useState('');
  const [loading, setLoading]   = useState(false);
  const navigate = useNavigate();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await apiClient.post('/v1/auth/login', { email, password });
      navigate('/');
    } catch {
      setError('Invalid email or password');
    } finally {
      setLoading(false);
    }
  };

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '14px 16px', fontSize: 16,
    border: '1px solid var(--stone)', background: 'var(--cream)',
    color: 'var(--ink)', fontFamily: 'inherit', outline: 'none',
    transition: 'border-color 0.15s', boxSizing: 'border-box',
  };

  return (
    <div style={{ minHeight: 'calc(100vh - 52px)', display: 'flex' }}>

      {/* Form */}
      <div style={{ width: 420, flexShrink: 0, borderRight: '1px solid var(--stone)', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '32px 32px 24px', borderBottom: '1px solid var(--stone)', background: 'var(--sand)' }}>
          <h1 style={{ margin: 0, fontSize: 28, fontWeight: 800, letterSpacing: '-0.02em' }}>Sign in</h1>
        </div>

        <form onSubmit={handleLogin} style={{ padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: 20 }}>
          {error && (
            <div style={{
              padding: '14px 16px', fontSize: 15, fontWeight: 600,
              color: 'var(--vermillion)', background: 'rgba(201,48,32,0.08)',
              border: '1px solid rgba(201,48,32,0.2)',
            }}>
              {error}
            </div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <label style={{ fontSize: 14, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--ink-muted)' }}>
              Email
            </label>
            <input
              type="email" value={email} required
              onChange={e => setEmail(e.target.value)}
              style={inputStyle}
              onFocus={e => (e.target.style.borderColor = 'var(--cobalt)')}
              onBlur={e => (e.target.style.borderColor = 'var(--stone)')}
            />
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <label style={{ fontSize: 14, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--ink-muted)' }}>
              Password
            </label>
            <input
              type="password" value={password} required
              onChange={e => setPassword(e.target.value)}
              style={inputStyle}
              onFocus={e => (e.target.style.borderColor = 'var(--cobalt)')}
              onBlur={e => (e.target.style.borderColor = 'var(--stone)')}
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            style={{
              padding: '16px', fontSize: 17, fontWeight: 700,
              background: loading ? 'var(--stone)' : 'var(--cobalt)',
              color: '#fff', border: 'none',
              cursor: loading ? 'wait' : 'pointer',
              marginTop: 4, transition: 'background 0.15s',
            }}
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>

      {/* Info panel */}
      <div style={{ flex: 1, background: 'var(--cobalt)', padding: '48px 48px', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'rgba(255,255,255,0.5)', marginBottom: 24 }}>
            JobPilot
          </div>
          <h2 style={{ margin: '0 0 20px', fontSize: 'clamp(28px, 3vw, 44px)', fontWeight: 800, color: '#fff', letterSpacing: '-0.02em', lineHeight: 1.2, maxWidth: '14ch' }}>
            Your autonomous career intelligence
          </h2>
          <p style={{ margin: 0, fontSize: 18, color: 'rgba(255,255,255,0.65)', lineHeight: 1.65, maxWidth: '38ch' }}>
            JobPilot continuously discovers, normalises, enriches, and scores job opportunities against your profile — surfacing only the signals worth your attention.
          </p>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[
            ['Today & Jobs', 'Visible without signing in'],
            ['System & Search', 'Visible without signing in'],
            ['Saved, Applied, Settings', 'Requires authentication'],
          ].map(([feature, desc]) => (
            <div key={feature} style={{ display: 'flex', gap: 16 }}>
              <span style={{ fontSize: 15, fontWeight: 700, color: '#fff', width: 200, flexShrink: 0 }}>{feature}</span>
              <span style={{ fontSize: 15, color: 'rgba(255,255,255,0.55)' }}>{desc}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
