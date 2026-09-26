import { useState, useEffect } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useProfile } from '@/hooks/useProfile';

export function Settings() {
  const { user, isLoading: authLoading } = useAuth();
  const { profile, isLoading, isError, updateProfileAsync, isUpdating } = useProfile();
  const [headline,   setHeadline]   = useState('');
  const [skills,     setSkills]     = useState('');
  const [experience, setExperience] = useState('');
  const [status,     setStatus]     = useState('');

  useEffect(() => {
    if (profile) {
      setHeadline(profile.headline ?? '');
      setSkills(profile.skills ?? '');
      setExperience(profile.experience_years?.toString() ?? '');
    }
  }, [profile]);

  if (authLoading) return null;
  if (!user) return <Navigate to="/signin" replace />;
  if (isLoading) return <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--ink-muted)' }}>Loading…</div>;
  if (isError)   return <div style={{ padding: '40px 32px', fontSize: 17, color: 'var(--vermillion)' }}>Error loading profile</div>;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus('');
    try {
      await updateProfileAsync({
        headline: headline || null,
        skills: skills || null,
        experience_years: experience ? parseInt(experience, 10) : null,
      });
      setStatus('saved');
    } catch {
      setStatus('error');
    }
  };

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '12px 14px', fontSize: 16,
    border: '1px solid var(--stone)', background: 'var(--cream)',
    color: 'var(--ink)', fontFamily: 'inherit', outline: 'none',
    transition: 'border-color 0.15s', boxSizing: 'border-box',
  };
  const labelStyle: React.CSSProperties = {
    fontSize: 14, fontWeight: 700, textTransform: 'uppercase',
    letterSpacing: '0.06em', color: 'var(--ink-muted)',
  };

  return (
    <div style={{ minHeight: 'calc(100vh - 52px)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '24px 32px 20px', borderBottom: '1px solid var(--stone)', background: 'var(--sand)' }}>
        <h1 style={{ margin: 0, fontSize: 32, fontWeight: 800, letterSpacing: '-0.02em' }}>Operator Profile</h1>
        <p style={{ margin: '8px 0 0', fontSize: 16, color: 'var(--ink-muted)' }}>
          Profile data is used to score incoming signals against your preferences.
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        style={{ display: 'flex', flexDirection: 'column', maxWidth: 600, padding: '0 32px' }}
      >
        {[
          { id: 'headline', label: 'Headline', value: headline, set: setHeadline, placeholder: 'e.g. Staff Engineer, open to remote roles', type: 'text' },
          { id: 'skills',   label: 'Skills (comma-separated)', value: skills, set: setSkills, placeholder: 'React, TypeScript, Go, Postgres', type: 'textarea' },
          { id: 'exp',      label: 'Years of experience', value: experience, set: setExperience, placeholder: '5', type: 'number' },
        ].map(({ id, label, value, set, placeholder, type }) => (
          <div key={id} style={{ padding: '24px 0', borderBottom: '1px solid var(--stone)', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <label htmlFor={id} style={labelStyle}>{label}</label>
            {type === 'textarea' ? (
              <textarea
                id={id} value={value}
                onChange={e => set(e.target.value)}
                placeholder={placeholder}
                style={{ ...inputStyle, minHeight: 96, resize: 'vertical' }}
                onFocus={e => (e.target.style.borderColor = 'var(--cobalt)')}
                onBlur={e => (e.target.style.borderColor = 'var(--stone)')}
              />
            ) : (
              <input
                id={id} type={type} value={value}
                onChange={e => set(e.target.value)}
                placeholder={placeholder}
                style={inputStyle}
                onFocus={e => (e.target.style.borderColor = 'var(--cobalt)')}
                onBlur={e => (e.target.style.borderColor = 'var(--stone)')}
              />
            )}
          </div>
        ))}

        <div style={{ padding: '24px 0', display: 'flex', alignItems: 'center', gap: 20 }}>
          <button
            type="submit"
            disabled={isUpdating}
            style={{
              padding: '14px 32px', fontSize: 16, fontWeight: 700,
              background: 'var(--cobalt)', color: '#fff', border: 'none',
              cursor: isUpdating ? 'wait' : 'pointer',
              opacity: isUpdating ? 0.7 : 1,
            }}
          >
            {isUpdating ? 'Saving…' : 'Save profile'}
          </button>
          {status === 'saved' && (
            <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--acid)' }}>Saved successfully</span>
          )}
          {status === 'error' && (
            <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--vermillion)' }}>Save failed — try again</span>
          )}
        </div>
      </form>
    </div>
  );
}
