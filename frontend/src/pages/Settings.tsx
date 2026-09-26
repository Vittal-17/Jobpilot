import { useState } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useProfile } from '@/hooks/useProfile';
import { PageState } from '@/components/PageState';
import { SectionHead } from '@/components/SectionHead';
import { Mark } from '@/components/Mark';

export function Settings() {
  const { user, isLoading: authLoading } = useAuth();
  const { profile, isLoading, isError, updateProfileAsync, isUpdating } = useProfile();
  const [headline,   setHeadline]   = useState('');
  const [skills,     setSkills]     = useState('');
  const [experience, setExperience] = useState('');
  const [status,     setStatus]     = useState('');
  const [lastProfile, setLastProfile] = useState(profile);

  // Seed the editable form from the fetched profile the first time it arrives
  // (and whenever it changes) — done during render, not in an effect.
  if (profile && profile !== lastProfile) {
    setLastProfile(profile);
    setHeadline(profile.headline ?? '');
    setSkills(profile.skills ?? '');
    setExperience(profile.experience_years?.toString() ?? '');
  }

  if (authLoading) return null;
  if (!user) return <Navigate to="/signin" replace />;

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

  const body = (() => {
    if (isLoading) {
      return <PageState eyebrow="Profile" title="Loading your profile…" body="Retrieving the vectors the engine scores incoming signals against." />;
    }
    if (isError) {
      return <PageState tone="error" eyebrow="Profile" title="Couldn't load your profile." body="Your operator profile is stored server-side and could not be retrieved. Try again once the connection settles." />;
    }
    return (
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 380px', flex: 1, minHeight: 0 }}>
        {/* Form column */}
        <div style={{ borderRight: '1px solid var(--stone)', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <form className="ed-form" onSubmit={handleSubmit} style={{ maxWidth: 620, padding: '38px 40px' }}>
            <div className="ed-field">
              <label htmlFor="pf-headline">Headline</label>
              <input id="pf-headline" className="ed-input" value={headline} onChange={e => setHeadline(e.target.value)} placeholder="e.g. Staff Engineer, open to remote roles" />
            </div>
            <div className="ed-field">
              <label htmlFor="pf-skills">Skills — comma separated</label>
              <textarea id="pf-skills" className="ed-textarea" value={skills} onChange={e => setSkills(e.target.value)} placeholder="React, TypeScript, Go, Postgres" />
            </div>
            <div className="ed-field">
              <label htmlFor="pf-exp">Years of experience</label>
              <input id="pf-exp" className="ed-input" type="number" value={experience} onChange={e => setExperience(e.target.value)} placeholder="5" />
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 20, marginTop: 4 }}>
              <button type="submit" className="ed-submit" disabled={isUpdating}>
                {isUpdating ? 'Saving…' : <>Save profile <span aria-hidden>→</span></>}
              </button>
              {status === 'saved' && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--mint)' }}>Saved</span>}
              {status === 'error' && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--vermillion)' }}>Save failed — try again</span>}
            </div>
          </form>
          <div className="hatch" style={{ flex: 1, borderTop: '1px solid var(--stone)', minHeight: 40 }} />
        </div>

        {/* Context rail — explains how the profile feeds scoring */}
        <div style={{ background: 'var(--sand)', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <div style={{ padding: '32px 28px', borderBottom: '1px solid var(--stone)' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--cobalt)', marginBottom: 20 }}>How scoring uses this</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
              {[
                ['01', 'Headline', 'var(--cobalt)', 'Sets the role identity the MATCH stage weighs each signal against.'],
                ['02', 'Skills', 'var(--violet)', 'Each skill becomes a vector; overlap with a posting lifts its match score.'],
                ['03', 'Experience', 'var(--amber)', 'Calibrates seniority so under- and over-levelled roles are down-weighted.'],
              ].map(([n, t, c, d]) => (
                <div key={t} style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, color: 'var(--stone-dark)', width: 18, flexShrink: 0, paddingTop: 3 }}>{n}</span>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: c, flexShrink: 0, marginTop: 7 }} />
                  <div>
                    <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink)' }}>{t}</div>
                    <div style={{ fontSize: 15, color: 'var(--ink-soft)', lineHeight: 1.55, marginTop: 2 }}>{d}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="hatch" style={{ flex: 1, minHeight: 40 }} />
        </div>
      </div>
    );
  })();

  return (
    <div className="page">
      <SectionHead
        index="07"
        kicker="Operator Profile"
        title={<>The vectors <em>you<Mark variant="scribble" /></em> set.</>}
        deck="Profile data is scored against every incoming signal — the sharper this is, the sharper the stream."
        aside={<><span className="u">Operator</span><span className="email">{user.email}</span></>}
      />
      {body}
    </div>
  );
}
