import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { SystemStatusResponse } from '@/api/types';

export function useSystemStatus() {
  return useQuery({
    queryKey: ['system', 'status'],
    queryFn: async () => {
      const { data } = await apiClient.get<SystemStatusResponse>('/v1/system/status');
      return data;
    },
    refetchInterval: 30000,
  });
}
