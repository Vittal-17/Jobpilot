import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { ProfileResponse, ProfileUpdate } from '@/api/types';

export function useProfile() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['profile'],
    queryFn: async () => {
      const { data } = await apiClient.get<ProfileResponse>('/v1/profile');
      return data;
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
