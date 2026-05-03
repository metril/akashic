// ESLint flat config (ESLint 9). Minimal: catch unused vars, dead
// imports, and react-hooks rule violations. tsconfig already enforces
// stricter type-level checks; this layer is for the things tsc can't
// see (unused destructured props, exhaustive-deps, etc.).
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

export default [
  {
    ignores: ["dist/**", "node_modules/**", "coverage/**"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.es2024,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      // tsc already covers some unused-vars cases; the eslint rule
      // catches the rest (e.g. destructured params). Use the TS
      // variant so it understands type-only imports.
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      // Loosened — Akashic uses `any` deliberately at a few seams
      // (api response casts, third-party callback shapes). Surface
      // them as warnings so they don't slip in unintentionally but
      // don't block CI on the existing baseline.
      "@typescript-eslint/no-explicit-any": "warn",
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
    },
  },
];
