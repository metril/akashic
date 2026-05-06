import type { AnyConfig, SourceType } from "./sourceTypes";
import { LocalFields } from "./source-fields/LocalFields";
import { NfsFields } from "./source-fields/NfsFields";
import { SshFields } from "./source-fields/SshFields";
import { SmbFields } from "./source-fields/SmbFields";
import { S3Fields } from "./source-fields/S3Fields";
import { PaperlessFields } from "./source-fields/PaperlessFields";
import { ImmichFields } from "./source-fields/ImmichFields";
import { AzureBlobFields } from "./source-fields/AzureBlobFields";
import { GCSFields } from "./source-fields/GCSFields";
import { WebDAVFields } from "./source-fields/WebDAVFields";
import { GDriveFields } from "./source-fields/GDriveFields";
import { OneDriveFields } from "./source-fields/OneDriveFields";

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
    case "paperless":
      return <PaperlessFields value={value as never} onChange={onChange as never} />;
    case "immich":
      return <ImmichFields value={value as never} onChange={onChange as never} />;
    case "azureblob":
      return <AzureBlobFields value={value as never} onChange={onChange as never} />;
    case "gcs":
      return <GCSFields value={value as never} onChange={onChange as never} />;
    case "webdav":
      return <WebDAVFields value={value as never} onChange={onChange as never} />;
    case "gdrive":
      return <GDriveFields value={value as never} onChange={onChange as never} />;
    case "onedrive":
      return <OneDriveFields value={value as never} onChange={onChange as never} />;
  }
}
