import type { components } from "./schema.generated";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type CapabilitiesResponse = components["schemas"]["CapabilitiesResponse"];
export type ArtifactListResponse = components["schemas"]["ArtifactListResponse"];
export type ArtifactDetailResponse = components["schemas"]["ArtifactDetailResponse"];
export type LineageManifestResponse = components["schemas"]["LineageManifestResponse"];
export type AssetCatalogResponse = components["schemas"]["AssetCatalogResponse"];
export type DataRequirementResponse = components["schemas"]["DataRequirementResponse"];

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = (await response.json()) as { message?: string };
      if (payload.message) message = payload.message;
    } catch {
      // A non-JSON proxy error still retains the status-based message.
    }
    throw new ApiClientError(message, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => getJson<HealthResponse>("/api/v2/health"),
  capabilities: () => getJson<CapabilitiesResponse>("/api/v2/capabilities"),
  assets: () => getJson<AssetCatalogResponse>("/api/v2/catalog/assets"),
  dataRequirements: () =>
    getJson<DataRequirementResponse>("/api/v2/catalog/data-requirements"),
  artifacts: (statuses = ["published"]) => {
    const search = new URLSearchParams();
    statuses.forEach((status) => search.append("status", status));
    search.set("limit", "100");
    return getJson<ArtifactListResponse>(`/api/v2/artifacts?${search.toString()}`);
  },
  artifact: (artifactId: string) =>
    getJson<ArtifactDetailResponse>(`/api/v2/artifacts/${artifactId}`),
  lineage: (artifactId: string) =>
    getJson<LineageManifestResponse>(`/api/v2/artifacts/${artifactId}/lineage`),
};
