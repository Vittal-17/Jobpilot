import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';

export function System() {
  const { data } = useQuery({
    queryKey: ['system'],
    queryFn: async () => {
      const { data } = await apiClient.get('/v1/system/status');
      return data;
    }
  });
  return <div className="max-w-5xl mx-auto"><h1 className="font-serif text-3xl">System Activity</h1><pre>{JSON.stringify(data, null, 2)}</pre></div>;
}
