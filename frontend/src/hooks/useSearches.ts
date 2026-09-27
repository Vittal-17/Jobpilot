import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { PaginatedUserSearchResponse, UserSearchCreate, UserSearchUpdate, UserSearchResponse } from '@/api/types';

export function useSearches(page = 1, size = 50, enabled = true) {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['searches', page, size],
    queryFn: async () => {
      const { data } = await apiClient.get<PaginatedUserSearchResponse>('/v1/searches', { params: { page, size } });
      return data;
    },
    enabled,
  });

  const createMutation = useMutation({
    mutationFn: async (payload: UserSearchCreate) => {
      const { data } = await apiClient.post<UserSearchResponse>('/v1/searches', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['searches'] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: async ({ id, payload }: { id: number; payload: UserSearchUpdate }) => {
      const { data } = await apiClient.patch<UserSearchResponse>(`/v1/searches/${id}`, payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['searches'] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: number) => {
      await apiClient.delete(`/v1/searches/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['searches'] });
    },
  });

  return {
    ...query,
    searches: query.data,
    createSearch: createMutation.mutateAsync,
    updateSearch: updateMutation.mutateAsync,
    deleteSearch: deleteMutation.mutateAsync,
    isCreating: createMutation.isPending,
    isUpdating: updateMutation.isPending,
    isDeleting: deleteMutation.isPending,
  };
}
