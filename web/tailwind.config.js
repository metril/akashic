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
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
        },
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(0 0 0 / 0.04), 0 1px 3px 0 rgb(0 0 0 / 0.06)",
      },
    },
  },
  plugins: [],
};
