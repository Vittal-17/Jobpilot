import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';

export function Feed() {
  const { data } = useQuery({
    queryKey: ['feed'],
    queryFn: async () => {
      const { data } = await apiClient.get('/v1/recommendations');
      return data;
    }
  });
  return <div className="max-w-5xl mx-auto"><h1 className="font-serif text-3xl">Job Feed</h1><pre>{JSON.stringify(data, null, 2)}</pre></div>;
}
