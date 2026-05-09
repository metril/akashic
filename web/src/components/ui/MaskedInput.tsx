import { forwardRef, useState } from "react";
import { cn } from "./cn";

/**
 * v0.21.0 — single-line secret input with a hide/show toggle.
 *
 * Wraps the same Tailwind shape as Input (kept in sync here rather
 * than recomposed via a Higher-Order pattern — the eye icon needs to
 * sit inside the same relative container as the input). Hidden by
 * default per the v0.21.0 plan: shoulder-surf safe, lines up with
 * browser autofill semantics. The eye toggle reveals plaintext.
 *
 * Use for single-line passwords, API keys, client secrets, SAS
 * tokens, etc. NOT for multi-line credentials — those keep their
 * plain ``<textarea>`` since hide/show on a multi-line block is
 * awkward and the existing "***" masked-sentinel pattern handles
 * the unchanged-on-blank case.
 */
interface MaskedInputProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: string;
  error?: string;
  hint?: string;
  containerClassName?: string;
}

const EyeIcon = ({ shown }: { shown: boolean }) =>
  shown ? (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-4 w-4"
    >
      <path d="M3 3l18 18" />
      <path d="M10.5 10.5a2 2 0 0 0 3 3" />
      <path d="M16.7 16.7A8.7 8.7 0 0 1 12 18c-5 0-9-4-10-6 0.6-1.3 2.2-3.4 4.6-5" />
      <path d="M9.4 5.6A8.6 8.6 0 0 1 12 5c5 0 9 4 10 6-0.5 1-1.5 2.5-3 4" />
    </svg>
  ) : (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-4 w-4"
    >
      <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );

export const MaskedInput = forwardRef<HTMLInputElement, MaskedInputProps>(
  function MaskedInput(
    { label, error, hint, className, containerClassName, id, ...rest },
    ref,
  ) {
    const [shown, setShown] = useState(false);
    const inputId = id || rest.name;
    return (
      <div className={cn("w-full", containerClassName)}>
        {label && (
          <label
            htmlFor={inputId}
            className="block text-xs font-medium text-fg-muted mb-1.5"
          >
            {label}
            {rest.required && (
              <span
                className="ml-0.5 text-rose-500"
                aria-hidden="true"
                title="Required"
              >
                *
              </span>
            )}
          </label>
        )}
        <div className="relative">
          <input
            ref={ref}
            id={inputId}
            {...rest}
            type={shown ? "text" : "password"}
            className={cn(
              "w-full h-10 rounded-lg border border-line bg-surface",
              "px-3 pr-10 text-sm text-fg placeholder:text-fg-subtle",
              "focus:outline-none focus:ring-2 focus:ring-accent-500/30 focus:border-accent-500",
              "disabled:bg-app disabled:text-fg-muted disabled:cursor-not-allowed",
              error && "border-rose-400 focus:ring-rose-500/30 focus:border-rose-500",
              className,
            )}
          />
          <button
            type="button"
            onClick={() => setShown((v) => !v)}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded text-fg-subtle hover:text-fg hover:bg-surface-muted transition-colors"
            aria-label={shown ? "Hide value" : "Show value"}
            title={shown ? "Hide" : "Show"}
            tabIndex={-1}
          >
            <EyeIcon shown={shown} />
          </button>
        </div>
        {error && <p className="text-xs text-rose-600 mt-1.5">{error}</p>}
        {!error && hint && (
          <p className="text-[11px] text-fg-muted mt-1">{hint}</p>
        )}
      </div>
    );
  },
);
