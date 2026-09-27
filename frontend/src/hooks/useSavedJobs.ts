import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { PaginatedSavedJobResponse, SavedJobResponse } from '@/api/types';

export function useSavedJobs(page = 1, size = 20) {
  return useQuery({
    queryKey: ['saved_jobs', page, size],
    queryFn: async () => {
      const { data } = await apiClient.get<PaginatedSavedJobResponse>('/v1/saved', { params: { page, size } });
      return data;
    },
  });
}

export function useSaveJob() {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: async (job_id: number) => {
      const { data } = await apiClient.post<SavedJobResponse>('/v1/saved', { job_id });
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['saved_jobs'] });
    },
  });

  return {
    saveJob: mutation.mutate,
    saveJobAsync: mutation.mutateAsync,
    isSaving: mutation.isPending,
    isError: mutation.isError,
    error: mutation.error,
  };
}

export function useUnsaveJob() {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: async (job_id: number) => {
      await apiClient.delete(`/v1/saved/${job_id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['saved_jobs'] });
    },
  });

  return {
    unsaveJob: mutation.mutate,
    unsaveJobAsync: mutation.mutateAsync,
    isUnsaving: mutation.isPending,
    isError: mutation.isError,
    error: mutation.error,
  };
}
