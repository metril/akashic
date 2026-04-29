import { Input } from "../../ui";
import type { FieldsProps, LocalConfig } from "../sourceTypes";

export function LocalFields({ value, onChange }: FieldsProps<LocalConfig>) {
  return (
    <Input
      label="Path"
      value={value.path ?? ""}
      onChange={(e) => onChange({ ...value, path: e.target.value })}
      placeholder="/home/user/documents"
      required
    />
  );
}
