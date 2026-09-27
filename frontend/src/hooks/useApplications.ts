import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { PaginatedApplicationResponse, ApplicationResponse, ApplicationUpdate } from '@/api/types';

export function useApplications(page = 1, size = 20) {
  return useQuery({
    queryKey: ['applications', page, size],
    queryFn: async () => {
      const { data } = await apiClient.get<PaginatedApplicationResponse>('/v1/applications', { params: { page, size } });
      return data;
    },
  });
}

export function useApplyJob() {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: async (job_id: number) => {
      const { data } = await apiClient.post<ApplicationResponse>('/v1/applications', { job_id });
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['applications'] });
    },
  });

  return {
    applyJob: mutation.mutate,
    applyJobAsync: mutation.mutateAsync,
    isApplying: mutation.isPending,
    isError: mutation.isError,
    error: mutation.error,
  };
}

export function useUpdateApplication() {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: async ({ id, status }: { id: number; status: ApplicationUpdate['status'] }) => {
      const { data } = await apiClient.patch<ApplicationResponse>(`/v1/applications/${id}`, { status });
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['applications'] });
    },
  });

  return {
    updateApplication: mutation.mutate,
    updateApplicationAsync: mutation.mutateAsync,
    isUpdating: mutation.isPending,
    isError: mutation.isError,
    error: mutation.error,
  };
}
