import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import {
  Card,
  CardHeader,
  Input,
  Button,
  EmptyState,
  Spinner,
  Badge,
} from "../components/ui";

interface Tag {
  id: string;
  name: string;
  color: string | null;
}

const COLOR_PRESETS = [
  { value: "",        label: "default" },
  { value: "#6366f1", label: "indigo" },
  { value: "#10b981", label: "emerald" },
  { value: "#f59e0b", label: "amber" },
  { value: "#ef4444", label: "red" },
  { value: "#8b5cf6", label: "violet" },
];

function TagPill({ tag }: { tag: Tag }) {
  // Color-when-set is rendered as a left dot + label; color-null falls
  // back to the neutral Badge styling.
  if (!tag.color) {
    return <Badge variant="neutral">{tag.name}</Badge>;
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-50 px-2 py-0.5 text-xs font-medium text-gray-700">
      <span
        className="size-2 rounded-full"
        style={{ backgroundColor: tag.color }}
        aria-hidden="true"
      />
      {tag.name}
    </span>
  );
}

export default function SettingsTags() {
  const qc = useQueryClient();
  const tagsQ = useQuery<Tag[]>({
    queryKey: ["tags"],
    queryFn: () => api.get<Tag[]>("/tags"),
  });

  const createTag = useMutation<Tag, Error, { name: string; color: string | null }>({
    mutationFn: (body) => api.post<Tag>("/tags", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tags"] }),
  });

  const deleteTag = useMutation<void, Error, string>({
    mutationFn: (id) => api.delete<void>(`/tags/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tags"] }),
  });

  const [name, setName] = useState("");
  const [color, setColor] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!name.trim()) {
      setFormError("Name is required");
      return;
    }
    try {
      await createTag.mutateAsync({ name: name.trim(), color: color || null });
      setName("");
      setColor("");
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to create tag");
    }
  }

  function handleDelete(tag: Tag) {
    if (confirm(`Delete tag "${tag.name}"? This removes it from any entries it's applied to.`)) {
      deleteTag.mutate(tag.id);
    }
  }

  return (
    <div className="px-8 py-7 max-w-3xl">
      <h1 className="text-2xl font-semibold text-gray-900 tracking-tight mb-1">
        Tags
      </h1>
      <p className="text-sm text-gray-500 mb-6">
        Custom labels you can apply to entries for filter and search.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        <div className="md:col-span-2">
          {tagsQ.isLoading ? (
            <div className="flex items-center justify-center py-12 text-gray-400">
              <Spinner />
            </div>
          ) : tagsQ.isError ? (
            <div className="text-sm text-rose-600 bg-rose-50 rounded px-3 py-2">
              {tagsQ.error instanceof Error
                ? tagsQ.error.message
                : "Failed to load tags"}
            </div>
          ) : (tagsQ.data ?? []).length === 0 ? (
            <div className="border border-gray-200 rounded py-12">
              <EmptyState
                title="No tags yet"
                description="Create one on the right. Tags can be applied to entries from the Browse drawer."
              />
            </div>
          ) : (
            <Card padding="none">
              <ul className="divide-y divide-gray-100">
                {(tagsQ.data ?? []).map((tag) => (
                  <li
                    key={tag.id}
                    className="flex items-center justify-between px-4 py-2.5"
                  >
                    <TagPill tag={tag} />
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => handleDelete(tag)}
                      loading={deleteTag.isPending && deleteTag.variables === tag.id}
                    >
                      Delete
                    </Button>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>

        <Card padding="md">
          <CardHeader title="Create a tag" />
          <form onSubmit={handleSubmit} className="space-y-3">
            <Input
              label="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="urgent"
              required
            />
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1.5">
                Color
              </label>
              <div className="flex flex-wrap gap-2">
                {COLOR_PRESETS.map((c) => (
                  <button
                    key={c.value || "default"}
                    type="button"
                    onClick={() => setColor(c.value)}
                    className={`size-7 rounded-full border-2 transition-colors ${
                      color === c.value
                        ? "border-gray-700"
                        : "border-transparent hover:border-gray-300"
                    }`}
                    style={{ backgroundColor: c.value || "#e5e7eb" }}
                    aria-label={c.label}
                    aria-pressed={color === c.value}
                    title={c.label}
                  />
                ))}
              </div>
            </div>
            {formError && (
              <p className="text-xs text-rose-600">{formError}</p>
            )}
            <Button
              type="submit"
              loading={createTag.isPending}
              className="w-full"
            >
              Create tag
            </Button>
          </form>
        </Card>
      </div>
    </div>
  );
}
