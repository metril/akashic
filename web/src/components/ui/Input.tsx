import { forwardRef } from "react";
import { cn } from "./cn";

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  leftIcon?: React.ReactNode;
  containerClassName?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, error, leftIcon, className, containerClassName, id, ...rest },
  ref,
) {
  const inputId = id || rest.name;
  return (
    <div className={cn("w-full", containerClassName)}>
      {label && (
        <label
          htmlFor={inputId}
          className="block text-xs font-medium text-fg-muted mb-1.5"
        >
          {label}
        </label>
      )}
      <div className="relative">
        {leftIcon && (
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-fg-subtle pointer-events-none">
            {leftIcon}
          </span>
        )}
        <input
          ref={ref}
          id={inputId}
          {...rest}
          className={cn(
            "w-full h-10 rounded-lg border border-line bg-surface",
            "px-3 text-sm text-fg placeholder:text-fg-subtle",
            "focus:outline-none focus:ring-2 focus:ring-accent-500/30 focus:border-accent-500",
            "disabled:bg-app disabled:text-fg-muted disabled:cursor-not-allowed",
            leftIcon ? "pl-9" : undefined,
            error && "border-rose-400 focus:ring-rose-500/30 focus:border-rose-500",
            className,
          )}
        />
      </div>
      {error && <p className="text-xs text-rose-600 mt-1.5">{error}</p>}
    </div>
  );
});
