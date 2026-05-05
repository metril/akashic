/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  // Class-based dark mode: a script in index.html sets `<html class="dark">`
  // before React mounts based on the saved preference, preventing the
  // white flash. The useTheme hook keeps that class in sync at runtime.
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '"Inter Variable"',
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
      colors: {
        // Semantic tokens — values come from CSS variables defined in
        // index.css under :root and :root.dark. Components use these
        // utility classes (`bg-surface`, `text-fg-muted`, etc.) so a
        // single token edit re-themes the whole app.
        app: "var(--color-app)",
        surface: {
          DEFAULT: "var(--color-surface)",
          muted: "var(--color-surface-muted)",
        },
        fg: {
          DEFAULT: "var(--color-fg)",
          muted: "var(--color-fg-muted)",
          subtle: "var(--color-fg-subtle)",
        },
        line: {
          DEFAULT: "var(--color-border)",
          subtle: "var(--color-border-subtle)",
        },
        accent: {
          50: "#eef2ff",
          100: "#e0e7ff",
          200: "#c7d2fe",
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
        },
        // v0.5.11 — modal scrim. Single color/opacity used by ModalShell,
        // CommandPalette, and Drawer. Pre-consolidation each had its own
        // bg-gray-900/45-or-/55 inline; now they all share `bg-scrim`.
        scrim: "rgb(15 23 42 / 0.5)",
        // v0.5.11 — treemap & analytics category palette. The Treemap GL
        // canvas needs hex strings (not class names) so the matching
        // CATEGORY_PALETTE constant in components/storage/treemapGL.ts
        // mirrors these exact values. Update both together.
        category: {
          1: "#6366f1",
          2: "#10b981",
          3: "#f59e0b",
          4: "#ef4444",
          5: "#8b5cf6",
          6: "#06b6d4",
          7: "#ec4899",
          8: "#84cc16",
          9: "#f97316",
          10: "#0ea5e9",
        },
        // v0.5.11 — semantic colors for treemap/analytics modes. heat-*
        // for age-of-files visuals, risk-* for permission-exposure
        // visuals. Hex values mirror treemapGL.ts colorFor().
        heat: {
          hot: "#10b981",
          warm: "#f59e0b",
          cold: "#94a3b8",
        },
        risk: {
          public: "#ef4444",
          authenticated: "#f59e0b",
          restricted: "#10b981",
        },
      },
      // v0.5.11 — typography scale tokens. Composite (size + line-height
      // + weight + tracking) so semantic class names like `text-meta`,
      // `text-h2` replace the ad-hoc `text-xs uppercase tracking-wider`
      // / `text-2xl font-semibold` combos scattered across pages.
      // Tailwind preserves all default sizes too, so existing classes
      // keep working while we migrate.
      fontSize: {
        meta: ["11px", { lineHeight: "16px", letterSpacing: "0.02em" }],
        label: ["12px", { lineHeight: "16px", fontWeight: "500" }],
        body: ["14px", { lineHeight: "20px" }],
        "body-strong": ["14px", { lineHeight: "20px", fontWeight: "500" }],
        h4: ["16px", { lineHeight: "24px", fontWeight: "600" }],
        h3: ["18px", { lineHeight: "26px", fontWeight: "600" }],
        h2: ["24px", { lineHeight: "32px", fontWeight: "600" }],
        h1: ["30px", { lineHeight: "36px", fontWeight: "700" }],
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(0 0 0 / 0.04), 0 1px 3px 0 rgb(0 0 0 / 0.06)",
      },
    },
  },
  plugins: [],
};
