import type {
  CloudDriveACL as CloudDriveACLType,
  CloudDriveGrant,
  CloudDriveRole,
} from "../../types";
import { Chip, Mono } from "./shared";

const ROLE_VARIANT: Record<CloudDriveRole, "allow" | "neutral" | "muted"> = {
  owner: "allow",
  writer: "allow",
  file_organizer: "allow",
  commenter: "neutral",
  reader: "neutral",
};

/**
 * Cloud-drive ACL — Drive / OneDrive / Dropbox.
 *
 * Renders the per-principal grant list. Compared to NT/POSIX, the
 * key differences are:
 *
 *  - "Anyone with the link" is a first-class principal type;
 *    surface it visibly (yellow chip) so admins notice public shares.
 *  - Domain-restricted grants get a banner above the table.
 *  - Each grant carries inherited / inherited_from_path; render
 *    inherited rows with a softer style and a "from /Foo" tooltip,
 *    matching the existing entry-tag inheritance pattern.
 */
export function CloudDriveACL({ acl }: { acl: CloudDriveACLType }) {
  return (
    <div>
      {acl.domain_restricted_to && (
        <p className="text-xs text-fg-muted mb-3">
          Sharing is constrained to domain{" "}
          <Mono>{acl.domain_restricted_to}</Mono> at scan time.
        </p>
      )}

      {acl.grants.length === 0 ? (
        <p className="text-sm text-fg-subtle italic">
          No sharing grants — only the file owner can access this entry.
        </p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[11px] text-fg-subtle uppercase tracking-wide">
              <th className="text-left py-1 font-semibold">Principal</th>
              <th className="text-left py-1 font-semibold">Role</th>
              <th className="text-left py-1 font-semibold">Source</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line-subtle">
            {acl.grants.map((g, i) => (
              <CloudDriveGrantRow key={i} grant={g} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function CloudDriveGrantRow({ grant }: { grant: CloudDriveGrant }) {
  const p = grant.principal;
  const label =
    p.type === "anyone"
      ? "Anyone with link"
      : p.type === "domain"
      ? `@${p.id}`
      : p.email || p.name || p.id;

  return (
    <tr className={grant.inherited ? "text-fg-muted" : ""}>
      <td className="py-1.5">
        <span className="font-medium text-fg">{label}</span>
        {p.type === "anyone" && (
          <span className="ml-2">
            <Chip variant="muted">public</Chip>
          </span>
        )}
        {p.type === "domain" && (
          <span className="ml-2">
            <Chip variant="muted">domain</Chip>
          </span>
        )}
        {p.type === "group" && (
          <span className="ml-2">
            <Chip variant="muted">group</Chip>
          </span>
        )}
        {p.email && p.email !== p.id && p.type === "user" && (
          <span className="text-[11px] text-fg-subtle ml-2">{p.id}</span>
        )}
        {grant.link && grant.link.scope !== "restricted" && (
          <span className="ml-2">
            <Chip variant="muted">link · {grant.link.scope}</Chip>
          </span>
        )}
      </td>
      <td className="py-1.5">
        <Chip variant={ROLE_VARIANT[grant.role] ?? "neutral"}>{grant.role}</Chip>
      </td>
      <td className="py-1.5 text-xs text-fg-muted">
        {grant.inherited
          ? grant.inherited_from_path
            ? `inherited from ${grant.inherited_from_path}`
            : "inherited"
          : "direct"}
      </td>
    </tr>
  );
}
