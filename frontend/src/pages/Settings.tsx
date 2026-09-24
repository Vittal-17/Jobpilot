import { useState, useEffect } from 'react';
import { useProfile } from '@/hooks/useProfile';

export function Settings() {
  const { profile, isLoading, isError, updateProfileAsync, isUpdating } = useProfile();

  const [headline, setHeadline] = useState('');
  const [skills, setSkills] = useState('');
  const [experience, setExperience] = useState('');
  const [saveStatus, setSaveStatus] = useState('');

  useEffect(() => {
    if (profile) {
      setHeadline(profile.headline || '');
      setSkills(profile.skills || '');
      setExperience(profile.experience_years?.toString() || '');
    }
  }, [profile]);

  if (isLoading) return <div className="max-w-3xl mx-auto animate-pulse h-32 bg-[var(--color-border)]"></div>;
  if (isError) return <div className="max-w-3xl mx-auto text-[var(--color-signal-error)]">Failed to load profile.</div>;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaveStatus('');
    try {
      await updateProfileAsync({
        headline: headline || null,
        skills: skills || null,
        experience_years: experience ? parseInt(experience, 10) : null
      });
      setSaveStatus('Profile updated successfully.');
    } catch {
      setSaveStatus('Failed to update profile.');
    }
  };

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="font-serif text-4xl mb-8">Profile Settings</h1>

      <form onSubmit={handleSubmit} className="space-y-6">
        <div>
          <label className="block text-sm text-[var(--color-text-secondary)] mb-2">Headline</label>
          <input
            type="text"
            value={headline}
            onChange={e => setHeadline(e.target.value)}
            className="w-full p-3 border border-[var(--color-border)] bg-transparent focus:outline-none focus:border-[var(--color-text-primary)]"
            placeholder="e.g. Senior Frontend Engineer"
          />
        </div>

        <div>
          <label className="block text-sm text-[var(--color-text-secondary)] mb-2">Skills (comma separated)</label>
          <textarea
            value={skills}
            onChange={e => setSkills(e.target.value)}
            className="w-full p-3 border border-[var(--color-border)] bg-transparent focus:outline-none focus:border-[var(--color-text-primary)] min-h-[100px]"
            placeholder="React, TypeScript, Node.js"
          />
        </div>

        <div>
          <label className="block text-sm text-[var(--color-text-secondary)] mb-2">Years of Experience</label>
          <input
            type="number"
            value={experience}
            onChange={e => setExperience(e.target.value)}
            className="w-full p-3 border border-[var(--color-border)] bg-transparent focus:outline-none focus:border-[var(--color-text-primary)]"
            placeholder="e.g. 5"
          />
        </div>

        <div className="flex items-center gap-4 pt-4">
          <button
            type="submit"
            disabled={isUpdating}
            className="px-6 py-2 bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] disabled:opacity-50 hover:bg-[var(--color-text-secondary)] transition-colors"
          >
            {isUpdating ? 'Saving...' : 'Save Profile'}
          </button>
          {saveStatus && <span className="text-sm text-[var(--color-text-secondary)]">{saveStatus}</span>}
        </div>
      </form>
    </div>
  );
}
