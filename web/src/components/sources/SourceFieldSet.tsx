import type { AnyConfig, SourceType } from "./sourceTypes";
import { LocalFields } from "./source-fields/LocalFields";
import { NfsFields } from "./source-fields/NfsFields";
import { SshFields } from "./source-fields/SshFields";
import { SmbFields } from "./source-fields/SmbFields";
import { S3Fields } from "./source-fields/S3Fields";

interface SourceFieldSetProps {
  type: SourceType;
  value: Partial<AnyConfig>;
  onChange: (next: Partial<AnyConfig>) => void;
}

/**
 * Per-type field rendering, extracted from AddSourceForm so the create
 * flow and the edit drawer share the same widgets. Adding a new source
 * type means editing one place.
 *
 * The individual *Fields components don't have a built-in "read-only"
 * mode — both create and edit paths just disable the form and rely on
 * the same controls. If we later add display-only rendering, swap the
 * branches here on a `mode` prop.
 */
export function SourceFieldSet({ type, value, onChange }: SourceFieldSetProps) {
  switch (type) {
    case "local":
      return <LocalFields value={value as never} onChange={onChange as never} />;
    case "nfs":
      return <NfsFields value={value as never} onChange={onChange as never} />;
    case "ssh":
      return <SshFields value={value as never} onChange={onChange as never} />;
    case "smb":
      return <SmbFields value={value as never} onChange={onChange as never} />;
    case "s3":
      return <S3Fields value={value as never} onChange={onChange as never} />;
  }
}
