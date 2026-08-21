import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";

export const V022_RELEASE_CONTROL_QUERY_KEY = ["v022-release-control"] as const;

export function useV022ReleaseControl() {
  return useQuery({
    queryKey: V022_RELEASE_CONTROL_QUERY_KEY,
    queryFn: api.releaseControl,
    retry: false,
    staleTime: 0,
    refetchInterval: 5_000,
    refetchOnWindowFocus: true,
  });
}
