import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { PaginatedJobResponse } from '@/api/types';

export function useJobs(page = 1, size = 20) {
  return useQuery({
    queryKey: ['jobs', page, size],
    queryFn: async () => {
      const { data } = await apiClient.get<PaginatedJobResponse>('/v1/jobs', {
        params: { page, size },
      });
      return data;
    },
  });
}
