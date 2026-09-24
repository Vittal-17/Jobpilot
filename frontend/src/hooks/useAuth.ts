import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { UserResponse } from '@/api/types';
import { useNavigate } from 'react-router-dom';

export function useAuth() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const query = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: async () => {
      const { data } = await apiClient.get<UserResponse>('/v1/me');
      return data;
    },
    retry: false, // Don't retry if 401
    staleTime: 5 * 60 * 1000,
  });

  const logoutMutation = useMutation({
    mutationFn: async () => {
      await apiClient.post('/v1/auth/logout');
    },
    onSuccess: () => {
      queryClient.clear();
      navigate('/login');
    },
  });

  return {
    user: query.data,
    isLoading: query.isLoading,
    isError: query.isError,
    logout: logoutMutation.mutate,
    isLoggingOut: logoutMutation.isPending,
  };
}
