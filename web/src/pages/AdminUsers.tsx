import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client, ok } from "../api/client";
import { useScopes } from "../hooks/useScopes";
import { formatRelative } from "../lib/format";
import type { User } from "../api/types";
import { Badge, Button, Page, Select, SectionState } from "../components/ui";

type Role = "viewer" | "admin";

const ROLE_OPTIONS = [
  { value: "viewer", label: "viewer" },
  { value: "admin", label: "admin" },
];

interface RowProps {
  user: User;
  canWrite: boolean;
  busy: boolean;
  error?: string;
  onRole: (u: User, role: Role) => void;
  onUnpin: (u: User) => void;
}

export function UserRow({ user, canWrite, busy, error, onRole, onUnpin }: RowProps) {
  return (
    <tr className="border-t border-line-subtle align-top">
      <td className="py-2 pr-3 text-sm font-medium text-fg">{user.username}</td>
      <td className="py-2 pr-3 text-sm text-fg-muted">{user.email ?? "—"}</td>
      <td className="py-2 pr-3 text-xs text-fg-muted">{user.auth_provider}</td>
      <td className="py-2 pr-3">
        {canWrite ? (
          <Select
            aria-label={`Role of ${user.username}`}
            value={user.role}
            disabled={busy}
            options={ROLE_OPTIONS}
            onChange={(e) => onRole(user, e.target.value as Role)}
          />
        ) : (
          <Badge variant={user.role === "admin" ? "info" : "neutral"}>{user.role}</Badge>
        )}
        {error && (
          <div role="alert" className="mt-1 text-xs text-rose-800 dark:text-rose-200">{error}</div>
        )}
      </td>
      <td className="py-2 pr-3">
        {user.role_pinned ? (
          <Badge variant="scanning" title="Set by an admin; IdP group mapping will not change it">pinned</Badge>
        ) : (
          <span className="text-xs text-fg-subtle">IdP / default</span>
        )}
      </td>
      <td className="py-2 pr-3 text-xs text-fg-muted">
        {user.last_login_at ? formatRelative(user.last_login_at) : "Never"}
      </td>
      <td className="py-2 text-right whitespace-nowrap">
        {canWrite && user.role_pinned && user.auth_provider !== "local" && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onUnpin(user)}>
            Return to IdP control
          </Button>
        )}
      </td>
    </tr>
  );
}

export default function AdminUsers() {
  const qc = useQueryClient();
  const { has } = useScopes();
  const canWrite = has("users:write");
  const [errors, setErrors] = useState<Record<string, string>>({});

  const users = useQuery({
    queryKey: ["admin-users"],
    queryFn: async () => ok(await client.GET("/api/v1/users")),
  });

  const patch = useMutation({
    mutationFn: async (v: { id: string; body: { role?: Role; role_pinned?: boolean } }) =>
      ok(await client.PATCH("/api/v1/users/{user_id}", { params: { path: { user_id: v.id } }, body: v.body })),
    onSuccess: (_d, v) => {
      setErrors((e) => ({ ...e, [v.id]: "" }));
      qc.invalidateQueries({ queryKey: ["admin-users"] });
    },
    onError: (err, v) => {
      setErrors((e) => ({ ...e, [v.id]: err instanceof Error ? err.message : "Change failed." }));
    },
  });

  const rows = users.data ?? [];
  return (
    <Page title="Users" description="Roles for every account. A role you set here is pinned: IdP group mapping leaves it alone." width="full">
      <SectionState loading={users.isLoading} error={users.error} empty={rows.length === 0} emptyTitle="No users">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="text-xs text-fg-muted">
                <th className="py-2 pr-3 font-medium">Username</th>
                <th className="py-2 pr-3 font-medium">Email</th>
                <th className="py-2 pr-3 font-medium">Provider</th>
                <th className="py-2 pr-3 font-medium">Role</th>
                <th className="py-2 pr-3 font-medium">Pinned</th>
                <th className="py-2 pr-3 font-medium">Last login</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <UserRow
                  key={u.id}
                  user={u}
                  canWrite={canWrite}
                  busy={patch.isPending && patch.variables?.id === u.id}
                  error={errors[u.id] || undefined}
                  onRole={(usr, role) => patch.mutate({ id: usr.id, body: { role } })}
                  onUnpin={(usr) => patch.mutate({ id: usr.id, body: { role_pinned: false } })}
                />
              ))}
            </tbody>
          </table>
        </div>
      </SectionState>
    </Page>
  );
}
