import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { ApiPage } from "./pages/ApiPage";
import { ArtifactDetailPage } from "./pages/ArtifactDetailPage";
import { ArtifactsPage } from "./pages/ArtifactsPage";
import { ExperimentsPage } from "./pages/ExperimentsPage";
import { ExperimentResultPage } from "./pages/ExperimentResultPage";
import { PlannedPage } from "./pages/PlannedPage";
import { RunsPage } from "./pages/RunsPage";

const GraphWorkspaceRoute = lazy(() => import("./workspace/GraphWorkspaceRoute"));
const GraphWorkspaceViewRoute = lazy(() => import("./workspace/GraphWorkspaceRoute").then((module) => ({
  default: module.GraphWorkspaceViewRoute,
})));
const ProductsPage = lazy(() => import("./pages/V022ProductsPage").then((module) => ({
  default: module.V022ProductsPage,
})));

function lazyRoute(element: React.ReactNode) {
  return <Suspense fallback={<p role="status">Loading page…</p>}>{element}</Suspense>;
}

function RedirectWithSearch({ pathname, hash }: { pathname: string; hash?: string }) {
  const location = useLocation();
  return <Navigate replace to={{ pathname, search: location.search, hash }} />;
}

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } },
});

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<RedirectWithSearch pathname="/research-context" />} />
        <Route path="assets" element={<RedirectWithSearch pathname="/research-context" />} />
        <Route path="data" element={<RedirectWithSearch pathname="/research-context" />} />
        <Route path="factors" element={<RedirectWithSearch pathname="/processing-1" />} />
        <Route path="signals" element={<RedirectWithSearch pathname="/processing-3" />} />
        <Route path="models" element={<RedirectWithSearch pathname="/aggregation" />} />
        <Route path="strategies" element={<RedirectWithSearch pathname="/strategy-configuration" />} />
        <Route element={lazyRoute(<GraphWorkspaceRoute />)}>
          <Route path="research-context" element={lazyRoute(<GraphWorkspaceViewRoute view="context" />)} />
          <Route path="processing-1" element={lazyRoute(<GraphWorkspaceViewRoute view={1} />)} />
          <Route path="processing-2" element={lazyRoute(<GraphWorkspaceViewRoute view={2} />)} />
          <Route path="processing-3" element={lazyRoute(<GraphWorkspaceViewRoute view={3} />)} />
          <Route path="aggregation" element={lazyRoute(<GraphWorkspaceViewRoute view="aggregation" />)} />
          <Route path="strategy-configuration" element={lazyRoute(<GraphWorkspaceViewRoute view="strategy" />)} />
          <Route path="experiment-launch" element={lazyRoute(<GraphWorkspaceViewRoute view="launch" />)} />
          <Route path="research-review" element={<RedirectWithSearch pathname="/strategy-configuration" hash="#configuration-review" />} />
        </Route>
        <Route path="workspace-v022" element={lazyRoute(<GraphWorkspaceRoute />)}>
          <Route index element={<Navigate replace to="processing-1" />} />
          <Route path="context" element={lazyRoute(<GraphWorkspaceViewRoute view="context" />)} />
          <Route path="processing-1" element={lazyRoute(<GraphWorkspaceViewRoute view={1} />)} />
          <Route path="processing-2" element={lazyRoute(<GraphWorkspaceViewRoute view={2} />)} />
          <Route path="processing-3" element={lazyRoute(<GraphWorkspaceViewRoute view={3} />)} />
          <Route path="aggregation" element={lazyRoute(<GraphWorkspaceViewRoute view="aggregation" />)} />
          <Route path="strategy" element={lazyRoute(<GraphWorkspaceViewRoute view="strategy" />)} />
          <Route path="launch" element={lazyRoute(<GraphWorkspaceViewRoute view="launch" />)} />
          <Route path="review" element={<RedirectWithSearch pathname="/workspace-v022/strategy" hash="#configuration-review" />} />
        </Route>
        <Route path="experiments" element={<ExperimentsPage />} />
        <Route path="experiments/results/:evidenceId" element={<ExperimentResultPage />} />
        <Route path="products" element={lazyRoute(<ProductsPage />)} />
        <Route path="products/:enrollmentId" element={lazyRoute(<ProductsPage />)} />
        <Route path="compare" element={lazyRoute(<ProductsPage />)} />
        <Route path="artifacts" element={<ArtifactsPage />} />
        <Route path="artifacts/:artifactId" element={<ArtifactDetailPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="api" element={<ApiPage />} />
        <Route path="*" element={<PlannedPage titleKey="nav.dashboard" milestone="M1D" />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return <QueryClientProvider client={queryClient}><BrowserRouter><AppRoutes /></BrowserRouter></QueryClientProvider>;
}
