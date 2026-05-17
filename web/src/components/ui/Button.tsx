import { forwardRef } from "react";
import { cn } from "./cn";
import { Spinner } from "./Spinner";

type Variant = "primary" | "secondary" | "danger" | "ghost";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  leftIcon?: React.ReactNode;
  /**
   * Reserve width for the longest label a stateful button can show, so
   * the button does not resize (and reflow its toolbar) when `children`
   * swaps — e.g. a "Pause"/"Resume" toggle, or a "Tag (N)" counter.
   * Pass the widest string the button will ever render; the visible
   * `children` are then laid over an invisible sizer of that width.
   */
  reserveLabel?: string;
}

const variantMap: Record<Variant, string> = {
  primary:
    "bg-accent-600 text-white hover:bg-accent-700 active:bg-accent-700 disabled:bg-accent-600/60",
  secondary:
    "bg-surface text-fg border border-line hover:bg-surface-muted active:bg-surface-muted disabled:opacity-60",
  danger:
    "bg-rose-50 text-rose-700 border border-rose-200 hover:bg-rose-100 active:bg-rose-200 disabled:opacity-60 dark:bg-rose-500/10 dark:text-rose-300 dark:border-rose-500/30 dark:hover:bg-rose-500/20",
  ghost:
    "bg-transparent text-fg-muted hover:bg-surface-muted active:bg-surface-muted disabled:opacity-60",
};

const sizeMap: Record<Size, string> = {
  sm: "h-8 px-3 text-sm",
  md: "h-10 px-4 text-sm",
  lg: "h-11 px-5 text-[15px]",
};

const gapMap: Record<Size, string> = {
  sm: "gap-1.5",
  md: "gap-2",
  lg: "gap-2",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "primary",
    size = "md",
    loading,
    leftIcon,
    reserveLabel,
    children,
    className,
    disabled,
    ...rest
  },
  ref,
) {
  // The visible content: leftIcon + label. Hidden (but still occupying
  // space) while loading so the spinner overlay doesn't change width.
  const content = (
    <span
      className={cn(
        "inline-flex items-center justify-center whitespace-nowrap",
        gapMap[size],
        loading && "invisible",
        // With a reserveLabel sizer in flow, the real content is laid
        // over it so it contributes no width of its own.
        reserveLabel && "absolute inset-0",
      )}
    >
      {leftIcon && <span className="flex-shrink-0">{leftIcon}</span>}
      {children}
    </span>
  );

  return (
    <button
      ref={ref}
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "relative inline-flex items-center justify-center font-medium rounded-lg",
        // whitespace-nowrap + shrink-0 by default: buttons inside tight
        // flex parents (e.g. `flex justify-between` toolbars) used to
        // squish or wrap their label across two lines. Callers that
        // genuinely want a wrapping/shrinking button can opt out via
        // className overrides. v0.5.6.
        "whitespace-nowrap shrink-0",
        "transition-colors duration-150",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2",
        "disabled:cursor-not-allowed",
        variantMap[variant],
        sizeMap[size],
        className,
      )}
    >
      {/* Invisible sizer — fixes the button's width to the longest label
          it can show, so a `children` swap never reflows the toolbar. */}
      {reserveLabel && (
        <span aria-hidden="true" className="invisible whitespace-nowrap">
          {reserveLabel}
        </span>
      )}
      {content}
      {loading && (
        <span className="absolute inset-0 inline-flex items-center justify-center">
          <Spinner size={size === "lg" ? "md" : "sm"} />
        </span>
      )}
    </button>
  );
});
