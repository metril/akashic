import React from "react";

export function Section({
  title,
  children,
  empty,
}: {
  title: string;
  children: React.ReactNode;
  empty?: boolean;
}) {
  return (
    <section className="px-6 py-4 border-b border-line-subtle last:border-b-0">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle mb-3">
        {title}
      </h3>
      {empty ? (
        <p className="text-sm text-fg-subtle italic">None</p>
      ) : (
        children
      )}
    </section>
  );
}

export function Subheader({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="text-[10px] font-semibold uppercase tracking-wider text-fg-subtle mt-4 mb-2">
      {children}
    </h4>
  );
}

export function Mono({ children }: { children: React.ReactNode }) {
  return (
    <code className="font-mono text-xs bg-surface-muted px-1.5 py-0.5 rounded text-fg">
      {children}
    </code>
  );
}

export function Chip({
  children,
  variant = "neutral",
}: {
  children: React.ReactNode;
  variant?: "neutral" | "allow" | "deny" | "muted";
}) {
  const styles: Record<string, string> = {
    neutral: "bg-surface-muted text-fg",
    allow:   "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300",
    deny:    "bg-red-50 text-red-700",
    muted:   "bg-app text-fg-muted",
  };
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-medium ${styles[variant]}`}
    >
      {children}
    </span>
  );
}
