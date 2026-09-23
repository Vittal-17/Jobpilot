import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiClient } from '@/api/client';

export function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const navigate = useNavigate();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiClient.post('/v1/auth/login', { email, password });
      navigate('/');
    } catch (err) {
      console.error(err);
      setError('Invalid credentials.');
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--color-bg-primary)]">
      <form onSubmit={handleLogin} className="p-8 border border-[var(--color-border)] bg-[var(--color-bg-secondary)] w-full max-w-sm">
        <h1 className="font-serif text-3xl mb-6 text-[var(--color-text-primary)]">JobPilot.</h1>
        {error && <div className="mb-4 text-[var(--color-signal-error)] text-sm">{error}</div>}
        <div className="mb-4">
          <label className="block text-sm font-sans mb-1 text-[var(--color-text-secondary)]">Email</label>
          <input
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            className="w-full p-2 border border-[var(--color-border)] focus:outline-none focus:border-[var(--color-text-primary)] bg-white"
            required
          />
        </div>
        <div className="mb-6">
          <label className="block text-sm font-sans mb-1 text-[var(--color-text-secondary)]">Password</label>
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            className="w-full p-2 border border-[var(--color-border)] focus:outline-none focus:border-[var(--color-text-primary)] bg-white"
            required
          />
        </div>
        <button type="submit" className="w-full bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] p-2 font-sans text-sm hover:bg-[var(--color-text-secondary)] transition-colors">
          Authenticate
        </button>
      </form>
    </div>
  );
}
