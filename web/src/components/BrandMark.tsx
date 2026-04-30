import { cn } from "./ui/cn";

interface BrandMarkProps {
  className?: string;
  showWordmark?: boolean;
}

export function BrandMark({ className, showWordmark = false }: BrandMarkProps) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <div className="relative h-9 w-9 rounded-xl bg-gradient-to-br from-accent-500 to-accent-700 flex items-center justify-center shadow-card">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="white"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-4.5 w-4.5"
          style={{ height: 18, width: 18 }}
        >
          <circle cx="12" cy="12" r="3.5" />
          <ellipse cx="12" cy="12" rx="9" ry="4" />
          <ellipse
            cx="12"
            cy="12"
            rx="9"
            ry="4"
            transform="rotate(60 12 12)"
          />
          <ellipse
            cx="12"
            cy="12"
            rx="9"
            ry="4"
            transform="rotate(120 12 12)"
          />
        </svg>
      </div>
      {showWordmark && (
        <span className="text-base font-semibold text-fg tracking-tight">
          Akashic
        </span>
      )}
    </div>
  );
}
