import { useState } from "react";
import { toast } from "sonner";

import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  Input,
  ModalShell,
  Page,
  Select,
  Spinner,
} from "../components/ui";
import {
  CredentialFields,
  type CredentialType,
  type CredentialValue,
} from "../components/credentials/CredentialFields";
import {
  useCreateCredentialProfile,
  useCredentialProfile,
  useCredentialProfiles,
  useDeleteCredentialProfile,
  useUpdateCredentialProfile,
  type CredentialProfileSummary,
} from "../hooks/useCredentialProfiles";

const TYPE_OPTIONS: { value: CredentialType; label: string }[] = [
  { value: "ssh", label: "SSH" },
  { value: "smb", label: "SMB" },
  { value: "nfs", label: "NFS" },
  { value: "s3", label: "S3" },
];

export default function SettingsCredentials() {
  const list = useCredentialProfiles();
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<CredentialProfileSummary | null>(null);
  const deleteMut = useDeleteCredentialProfile();

  async function handleDelete(profile: CredentialProfileSummary) {
    try {
      await deleteMut.mutateAsync(profile.id);
      toast.success(`Deleted profile "${profile.name}".`);
      setConfirmDelete(null);
    } catch (e) {
      toast.error(
        `Couldn't delete profile: ${e instanceof Error ? e.message : "unknown error"}.`,
      );
    }
  }

  return (
    <Page
      title="Credential profiles"
      description="Reusable credential bundles. Attach a profile to any number of hosts and shares — change the secret once, every reference picks it up."
      width="default"
    >
      <div className="flex items-center justify-end mb-3">
        <Button onClick={() => setCreating(true)}>+ New profile</Button>
      </div>

      {list.isLoading && (
        <div className="flex items-center justify-center py-12 text-fg-subtle">
          <Spinner />
        </div>
      )}

      {list.isError && (
        <Card padding="md">
          <p className="text-sm text-rose-600">
            {list.error instanceof Error
              ? list.error.message
              : "Failed to load credential profiles"}
          </p>
        </Card>
      )}

      {list.data && list.data.length === 0 && !list.isLoading && (
        <Card padding="lg">
          <EmptyState
            title="No credential profiles yet"
            description="Create one to share an SSH key, SMB password, or S3 access key across multiple hosts and shares."
          />
        </Card>
      )}

      {list.data && list.data.length > 0 && (
        <Card padding="none">
          <ul className="divide-y divide-line-subtle">
            {list.data.map((p) => (
              <li
                key={p.id}
                className="px-4 py-3 flex items-center justify-between gap-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-medium text-fg truncate">
                      {p.name}
                    </span>
                    <Badge variant="neutral">{p.type.toUpperCase()}</Badge>
                  </div>
                  {p.description && (
                    <p className="mt-1 text-xs text-fg-muted truncate">
                      {p.description}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setEditingId(p.id)}
                  >
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => setConfirmDelete(p)}
                  >
                    Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {creating && (
        <ProfileCreateModal onClose={() => setCreating(false)} />
      )}
      {editingId && (
        <ProfileEditModal
          profileId={editingId}
          onClose={() => setEditingId(null)}
        />
      )}
      <ConfirmDialog
        open={confirmDelete !== null}
        title={
          confirmDelete
            ? `Delete profile "${confirmDelete.name}"?`
            : "Delete profile?"
        }
        description="If any host or share references this profile, the delete will be refused. Reassign or unlink them first."
        confirmLabel="Delete"
        destructive
        loading={deleteMut.isPending}
        onConfirm={() => confirmDelete && handleDelete(confirmDelete)}
        onCancel={() =>
          !deleteMut.isPending && setConfirmDelete(null)
        }
      />
    </Page>
  );
}

function ProfileCreateModal({ onClose }: { onClose: () => void }) {
  const create = useCreateCredentialProfile();
  const [name, setName] = useState("");
  const [type, setType] = useState<CredentialType>("ssh");
  const [description, setDescription] = useState("");
  const [credentials, setCredentials] = useState<CredentialValue>({});

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await create.mutateAsync({
        name: name.trim(),
        type,
        credentials,
        description: description.trim() || null,
      });
      toast.success(`Created profile "${name}".`);
      onClose();
    } catch (e) {
      toast.error(
        `Couldn't create profile: ${e instanceof Error ? e.message : "unknown error"}.`,
      );
    }
  }

  return (
    <ModalShell open onClose={onClose} maxWidth="lg" ariaLabelledBy="cp-create-title">
      <form onSubmit={handleSubmit} className="p-5 space-y-3">
        <div>
          <p className="text-xs text-fg-muted">Settings → Credentials</p>
          <h2 id="cp-create-title" className="text-base font-semibold text-fg">
            New credential profile
          </h2>
        </div>
        <Input
          label="Profile name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="ssh-prod"
          required
          hint="A short label — e.g. 'ssh-prod' or 'smb-fileserver'."
        />
        <Select
          label="Type"
          value={type}
          onChange={(e) => {
            setType(e.target.value as CredentialType);
            // Clear creds when switching type so unrelated keys don't leak.
            setCredentials({});
          }}
          options={TYPE_OPTIONS}
        />
        <CredentialFields
          type={type}
          value={credentials}
          onChange={setCredentials}
        />
        <Input
          label="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Production SSH key — rotate quarterly"
        />
        <div className="flex justify-end gap-2 pt-2">
          <Button
            type="button"
            variant="ghost"
            onClick={onClose}
            disabled={create.isPending}
          >
            Cancel
          </Button>
          <Button type="submit" loading={create.isPending}>
            Create
          </Button>
        </div>
      </form>
    </ModalShell>
  );
}

function ProfileEditModal({
  profileId,
  onClose,
}: {
  profileId: string;
  onClose: () => void;
}) {
  const detail = useCredentialProfile(profileId);
  const update = useUpdateCredentialProfile();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [credentials, setCredentials] = useState<CredentialValue>({});
  const [hydrated, setHydrated] = useState(false);

  if (detail.data && !hydrated) {
    setName(detail.data.name);
    setDescription(detail.data.description ?? "");
    setCredentials(detail.data.credentials);
    setHydrated(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!detail.data) return;
    try {
      await update.mutateAsync({
        id: profileId,
        body: {
          name: name.trim(),
          description: description.trim() || null,
          credentials,
        },
      });
      toast.success(`Saved profile "${name}".`);
      onClose();
    } catch (e) {
      toast.error(
        `Couldn't save profile: ${e instanceof Error ? e.message : "unknown error"}.`,
      );
    }
  }

  return (
    <ModalShell open onClose={onClose} maxWidth="lg" ariaLabelledBy="cp-edit-title">
      <form onSubmit={handleSubmit} className="p-5 space-y-3">
        <div>
          <p className="text-xs text-fg-muted">Settings → Credentials</p>
          <h2 id="cp-edit-title" className="text-base font-semibold text-fg">
            Edit credential profile
          </h2>
        </div>
        {detail.isLoading && (
          <div className="flex items-center justify-center py-8" role="status" aria-label="Loading profile">
            <Spinner />
          </div>
        )}
        {detail.isError && (
          <div
            role="alert"
            className="rounded-md border border-rose-200 dark:border-rose-700/40 bg-rose-50 dark:bg-rose-950/30 px-3 py-2 text-sm text-rose-800 dark:text-rose-200"
          >
            {detail.error instanceof Error
              ? detail.error.message
              : "Couldn't load profile."}
          </div>
        )}
        {detail.data && (
          <>
            <Input
              label="Profile name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <p className="text-xs text-fg-muted">
              Type: <span className="font-medium">{detail.data.type.toUpperCase()}</span>{" "}
              — type is fixed once a profile is created. Create a new profile
              to change types.
            </p>
            <CredentialFields
              type={detail.data.type}
              value={credentials}
              onChange={setCredentials}
            />
            <Input
              label="Description (optional)"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button
            type="button"
            variant="ghost"
            onClick={onClose}
            disabled={update.isPending}
          >
            Cancel
          </Button>
          <Button type="submit" loading={update.isPending} disabled={!detail.data}>
            Save
          </Button>
        </div>
      </form>
    </ModalShell>
  );
}
