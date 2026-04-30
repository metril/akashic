import { forwardRef } from "react";
import { cn } from "./cn";

interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  options: SelectOption[];
  containerClassName?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, options, className, containerClassName, id, ...rest },
  ref,
) {
  const selectId = id || rest.name;
  return (
    <div className={cn("w-full", containerClassName)}>
      {label && (
        <label
          htmlFor={selectId}
          className="block text-xs font-medium text-fg-muted mb-1.5"
        >
          {label}
        </label>
      )}
      <select
        ref={ref}
        id={selectId}
        {...rest}
        className={cn(
          "w-full h-10 rounded-lg border border-line bg-surface",
          "px-3 pr-9 text-sm text-fg",
          "focus:outline-none focus:ring-2 focus:ring-accent-500/30 focus:border-accent-500",
          "appearance-none bg-no-repeat bg-[right_0.7rem_center] bg-[length:1em_1em]",
          "bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2020%2020%22%20fill%3D%22none%22%20stroke%3D%22%236b7280%22%20stroke-width%3D%221.5%22%3E%3Cpath%20d%3D%22M6%208l4%204%204-4%22%2F%3E%3C%2Fsvg%3E')]",
          className,
        )}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  );
});
