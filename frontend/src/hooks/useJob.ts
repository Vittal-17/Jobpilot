import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { JobResponse } from '@/api/types';

export function useJob(jobId?: number) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: async () => {
      if (!jobId) throw new Error('No job ID provided');
      const { data } = await apiClient.get<JobResponse>(`/v1/jobs/${jobId}`);
      return data;
    },
    enabled: !!jobId,
  });
}
