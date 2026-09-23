import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';

export function Settings() {
  const { data } = useQuery({
    queryKey: ['profile'],
    queryFn: async () => {
      const { data } = await apiClient.get('/v1/profile');
      return data;
    }
  });
  return <div className="max-w-5xl mx-auto"><h1 className="font-serif text-3xl">Profile Settings</h1><pre>{JSON.stringify(data, null, 2)}</pre></div>;
}
