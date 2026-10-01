import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getTechniqueCatalog, getTechniqueFavorites, setTechniqueFavorite } from '@/api/techniqueLibrary';
import { useAuthStore } from '@/stores/auth-store';

/** All library instances share one account-scoped cache and mutation queue. */
export function useTechniqueLibrary(username: string | null) {
  const client = useQueryClient();
  const key = ['technique-favorites', username] as const;
  const busy = useIsMutating({ mutationKey: key }) > 0;
  const catalog = useQuery({ queryKey: ['technique-catalog', username],
    queryFn: ({ signal }) => getTechniqueCatalog(signal), staleTime: 60_000, retry: false });
  const favorites = useQuery({ queryKey: key, queryFn: ({ signal }) => getTechniqueFavorites(signal),
    staleTime: 60_000, retry: false, enabled: !busy });
  const mutation = useMutation({ mutationKey: key, scope: { id: `technique-favorites:${username}` },
    mutationFn: async ({ id, favorite }: { id: string; favorite: boolean }) => {
      // A queued click must never run using a subsequent account's cookie.
      if (useAuthStore.getState().username !== username) throw new Error('Account changed');
      await client.cancelQueries({ queryKey: key });
      return setTechniqueFavorite(id, favorite);
    },
    onSuccess: (data) => {
      if (useAuthStore.getState().username === username) client.setQueryData(key, data);
    },
  });
  return { catalog, favorites, busy, mutation };
}
