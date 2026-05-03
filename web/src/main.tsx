import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
});

// Per-prefix overrides: 30 s is a reasonable default for "anything we
// don't know better about", but several data classes are misserved by
// it. Sources rarely change and don't warrant a refetch every 30 s on
// every page load; admin-audit is the opposite — admins watching it
// expect near-real-time freshness. Centralised here rather than
// repeated at every useQuery call site.
const FIVE_MINUTES = 5 * 60_000;
const ONE_MINUTE = 60_000;
const FIVE_SECONDS = 5_000;

queryClient.setQueryDefaults(["sources"], { staleTime: FIVE_MINUTES });
queryClient.setQueryDefaults(["scanners"], { staleTime: FIVE_MINUTES });
queryClient.setQueryDefaults(["server-setting"], { staleTime: FIVE_MINUTES });
queryClient.setQueryDefaults(["users"], { staleTime: FIVE_MINUTES });
queryClient.setQueryDefaults(["principals"], { staleTime: FIVE_MINUTES });
// Analytics charts don't need second-by-second freshness — the storage
// view is the live one. Five minutes prevents a refetch from firing
// mid-tooltip-hover and stalling the chart.
queryClient.setQueryDefaults(["analytics"], { staleTime: FIVE_MINUTES });
queryClient.setQueryDefaults(["dashboard"], { staleTime: ONE_MINUTE });
queryClient.setQueryDefaults(["admin-audit"], { staleTime: FIVE_SECONDS });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </BrowserRouter>
  </React.StrictMode>
);
