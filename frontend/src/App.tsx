import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { ApiPage } from "./pages/ApiPage";
import { AssetsPage } from "./pages/AssetsPage";
import { ArtifactDetailPage } from "./pages/ArtifactDetailPage";
import { ArtifactsPage } from "./pages/ArtifactsPage";
import { FactorsPage } from "./pages/FactorsPage";
import { ExperimentsPage } from "./pages/ExperimentsPage";
import { ProductsPage } from "./pages/ProductsPage";
import { PlannedPage } from "./pages/PlannedPage";
import { SignalsPage } from "./pages/SignalsPage";
import { ModelsPage } from "./pages/ModelsPage";
import { StrategiesPage } from "./pages/StrategiesPage";
import { WorkspacePage } from "./pages/WorkspacePage";
import { RunsPage } from "./pages/RunsPage";
import { WorkspaceSelectionProvider } from "./workspace/WorkspaceSelectionContext";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } },
});

export function AppRoutes() {
  return (
    <WorkspaceSelectionProvider><Routes>
      <Route element={<AppShell />}>
        <Route index element={<WorkspacePage />} />
        <Route path="assets" element={<AssetsPage />} />
        <Route path="factors" element={<FactorsPage />} />
        <Route path="signals" element={<SignalsPage />} />
        <Route path="models" element={<ModelsPage />} />
        <Route path="strategies" element={<StrategiesPage />} />
        <Route path="experiments" element={<ExperimentsPage />} />
        <Route path="products" element={<ProductsPage />} />
        <Route path="products/:enrollmentId" element={<ProductsPage />} />
        <Route path="data" element={<WorkspacePage />} />
        <Route path="compare" element={<ProductsPage />} />
        <Route path="artifacts" element={<ArtifactsPage />} />
        <Route path="artifacts/:artifactId" element={<ArtifactDetailPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="api" element={<ApiPage />} />
        <Route path="*" element={<PlannedPage titleKey="nav.dashboard" milestone="M1D" />} />
      </Route>
    </Routes></WorkspaceSelectionProvider>
  );
}

export default function App() {
  return <QueryClientProvider client={queryClient}><BrowserRouter><AppRoutes /></BrowserRouter></QueryClientProvider>;
}
