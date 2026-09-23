import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';

export function Search() {
  const { data } = useQuery({
    queryKey: ['searches'],
    queryFn: async () => {
      const { data } = await apiClient.get('/v1/searches');
      return data;
    }
  });
  return <div className="max-w-5xl mx-auto"><h1 className="font-serif text-3xl">Search Taxonomy</h1><pre>{JSON.stringify(data, null, 2)}</pre></div>;
}
