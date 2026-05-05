import { useEffect, useRef } from "react";
import { Button } from "./Button";
import { ModalShell } from "./ModalShell";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  // Focus the destructive/primary button on open so Enter confirms and
  // Tab cycles within the dialog.
  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
  }, [open]);

  return (
    <ModalShell
      open={open}
      onClose={onCancel}
      blocking={loading}
      ariaLabelledBy="confirm-title"
    >
      <div className="p-5">
        <h2
          id="confirm-title"
          className="text-base font-semibold text-fg"
        >
          {title}
        </h2>
        {description && (
          <div className="mt-2 text-sm text-fg-muted">{description}</div>
        )}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={onCancel}
            disabled={loading}
          >
            {cancelLabel}
          </Button>
          <Button
            ref={confirmRef}
            variant={destructive ? "danger" : "primary"}
            size="sm"
            onClick={onConfirm}
            loading={loading}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </ModalShell>
  );
}
