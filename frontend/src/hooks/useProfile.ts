import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { ProfileResponse, ProfileUpdate } from '@/api/types';

export function useProfile() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['profile'],
    queryFn: async () => {
      try {
        const { data } = await apiClient.get<ProfileResponse>('/v1/profile');
        return data;
      } catch (error: any) {
        // 404 means the authenticated user simply has no profile row yet.
        // Represent that as an empty (but valid) editable profile rather than
        // an error — PATCH /v1/profile will create it on first save. Any other
        // failure (401/500/network) still propagates as a genuine error.
        if (error?.response?.status === 404) {
          return {} as ProfileResponse;
        }
        throw error;
      }
    },
    retry: 1
  });

  const updateMutation = useMutation({
    mutationFn: async (updates: ProfileUpdate) => {
      const { data } = await apiClient.patch<ProfileResponse>('/v1/profile', updates);
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['profile'], data);
    },
  });

  return {
    profile: query.data,
    isLoading: query.isLoading,
    isError: query.isError,
    updateProfile: updateMutation.mutate,
    updateProfileAsync: updateMutation.mutateAsync,
    isUpdating: updateMutation.isPending,
  };
}
