import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { ApiPage } from "./pages/ApiPage";
import { AssetsPage } from "./pages/AssetsPage";
import { ArtifactDetailPage } from "./pages/ArtifactDetailPage";
import { ArtifactsPage } from "./pages/ArtifactsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DataPage } from "./pages/DataPage";
import { FactorsPage } from "./pages/FactorsPage";
import { PlannedPage } from "./pages/PlannedPage";
import { SignalsPage } from "./pages/SignalsPage";
import { ModelsPage } from "./pages/ModelsPage";
import { StrategiesPage } from "./pages/StrategiesPage";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } },
});

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<DashboardPage />} />
        <Route path="assets" element={<AssetsPage />} />
        <Route path="data" element={<DataPage />} />
        <Route path="factors" element={<FactorsPage />} />
        <Route path="signals" element={<SignalsPage />} />
        <Route path="models" element={<ModelsPage />} />
        <Route path="strategies" element={<StrategiesPage />} />
        <Route path="experiments" element={<PlannedPage titleKey="nav.experiments" milestone="M7" />} />
        <Route path="compare" element={<PlannedPage titleKey="nav.compare" milestone="M8" />} />
        <Route path="artifacts" element={<ArtifactsPage />} />
        <Route path="artifacts/:artifactId" element={<ArtifactDetailPage />} />
        <Route path="runs" element={<PlannedPage titleKey="nav.runs" milestone="M7" />} />
        <Route path="api" element={<ApiPage />} />
        <Route path="*" element={<PlannedPage titleKey="nav.dashboard" milestone="M1D" />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return <QueryClientProvider client={queryClient}><BrowserRouter><AppRoutes /></BrowserRouter></QueryClientProvider>;
}
