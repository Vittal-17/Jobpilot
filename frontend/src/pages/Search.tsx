import { useState } from 'react';
import { useSearches } from '@/hooks/useSearches';

export function Search() {
  const { searches, isLoading, isError, createSearch, deleteSearch, updateSearch, isCreating } = useSearches();

  const [query, setQuery] = useState('');
  const [location, setLocation] = useState('');
  const [remoteOnly, setRemoteOnly] = useState(false);

  if (isLoading) {
    return <div className="max-w-4xl mx-auto animate-pulse h-32 bg-[var(--color-border)]"></div>;
  }

  if (isError) {
    return <div className="max-w-4xl mx-auto text-[var(--color-signal-error)]">Failed to load searches.</div>;
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() && !location.trim()) return;
    await createSearch({ query, location, remote_only: remoteOnly, enabled: true });
    setQuery('');
    setLocation('');
    setRemoteOnly(false);
  };

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="font-serif text-4xl mb-8">Search Taxonomy</h1>

      <form onSubmit={handleCreate} className="mb-12 p-6 border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
        <h2 className="font-serif text-xl mb-4">Add Target</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <div>
            <label className="block text-xs text-[var(--color-text-secondary)] mb-1">Keywords</label>
            <input
              value={query} onChange={e => setQuery(e.target.value)}
              className="w-full p-2 text-sm border border-[var(--color-border)] bg-white focus:outline-none focus:border-[var(--color-text-primary)]"
              placeholder="e.g. Senior Product Designer"
            />
          </div>
          <div>
            <label className="block text-xs text-[var(--color-text-secondary)] mb-1">Location</label>
            <input
              value={location} onChange={e => setLocation(e.target.value)}
              className="w-full p-2 text-sm border border-[var(--color-border)] bg-white focus:outline-none focus:border-[var(--color-text-primary)]"
              placeholder="e.g. New York, NY"
            />
          </div>
        </div>
        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={remoteOnly} onChange={e => setRemoteOnly(e.target.checked)} />
            Remote Only
          </label>
          <button
            type="submit"
            disabled={isCreating || (!query.trim() && !location.trim())}
            className="px-4 py-2 bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] text-sm disabled:opacity-50 hover:bg-[var(--color-text-secondary)] transition-colors"
          >
            {isCreating ? 'Adding...' : 'Add Target'}
          </button>
        </div>
      </form>

      <div className="space-y-4">
        {!searches?.items.length ? (
          <div className="text-sm text-[var(--color-text-secondary)] italic">No search targets defined.</div>
        ) : (
          searches.items.map(search => (
            <div key={search.id} className="p-4 flex items-center justify-between border border-[var(--color-border)] bg-white/40">
              <div>
                <div className="font-medium text-lg mb-1">{search.query || '*'}</div>
                <div className="text-sm text-[var(--color-text-secondary)] flex gap-3">
                  <span>{search.location || 'Any Location'}</span>
                  {search.remote_only && <span>• Remote</span>}
                </div>
              </div>
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={search.enabled}
                    onChange={e => updateSearch({ id: search.id, payload: { enabled: e.target.checked }})}
                  />
                  Active
                </label>
                <button
                  onClick={() => deleteSearch(search.id)}
                  className="text-xs text-[var(--color-signal-error)] hover:underline"
                >
                  Delete
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
