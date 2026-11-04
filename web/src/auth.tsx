import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Center, Loader } from '@mantine/core';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { api, ApiError, type User } from './api';

export const ME_KEY = ['me'];

/** The logged-in user, or null when logged out. */
export function useMe() {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: async () => {
      try {
        return await api<User>('/auth/me');
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
    staleTime: Infinity,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; password: string }) =>
      api<User>('/auth/login', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: (user) => qc.setQueryData(ME_KEY, user),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>('/auth/logout', { method: 'POST' }),
    onSettled: () => {
      qc.clear();
      qc.setQueryData(ME_KEY, null);
    },
  });
}

function FullPageLoader() {
  return (
    <Center h="100vh">
      <Loader />
    </Center>
  );
}

/** Route guard: sends logged-out visitors to /login, and non-admins away from admin pages. */
export function RequireAuth({ admin = false }: { admin?: boolean }) {
  const { data: me, isPending } = useMe();
  const location = useLocation();
  if (isPending) return <FullPageLoader />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (admin && !me.is_admin) return <Navigate to="/" replace />;
  return <Outlet />;
}
