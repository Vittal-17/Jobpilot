import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { PaginatedRecommendedJobResponse } from '@/api/types';

export function useRecommendations(page = 1, size = 20) {
  return useQuery({
    queryKey: ['recommendations', page, size],
    queryFn: async () => {
      const { data } = await apiClient.get<PaginatedRecommendedJobResponse>('/v1/recommendations', {
        params: { page, size },
      });
      return data;
    },
  });
}
