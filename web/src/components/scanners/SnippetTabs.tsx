/**
 * Tabbed paste-target snippets shown in step 2 of the join-token
 * wizard. Each tab renders the same token in a different idiom
 * (shell command, docker run, compose, k8s, env file). Server
 * renders the snippet text so formatting stays consistent across
 * the five flavours and we can version it cleanly.
 */
import { useState } from "react";

import { Button } from "../ui";

interface Props {
  snippets: {
    shell: string;
    docker_run: string;
    compose: string;
    k8s: string;
    env: string;
  };
}

const TABS: { key: keyof Props["snippets"]; label: string }[] = [
  { key: "shell", label: "Shell" },
  { key: "docker_run", label: "docker run" },
  { key: "compose", label: "compose" },
  { key: "k8s", label: "k8s" },
  { key: "env", label: "env file" },
];

export function SnippetTabs({ snippets }: Props) {
  const [active, setActive] = useState<keyof Props["snippets"]>("shell");
  const [copied, setCopied] = useState(false);

  function copy() {
    navigator.clipboard.writeText(snippets[active]).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div>
      <div role="tablist" className="flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button
            type="button"
            key={t.key}
            role="tab"
            aria-selected={active === t.key}
            onClick={() => { setActive(t.key); setCopied(false); }}
            className={`px-3 py-1.5 text-xs font-medium rounded-t -mb-px ${
              active === t.key
                ? "border border-line border-b-surface bg-surface text-fg"
                : "text-fg-muted hover:text-fg"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <pre className="text-[11px] font-mono whitespace-pre-wrap break-words bg-app border border-line border-t-0 rounded-b p-3 max-h-72 overflow-auto text-fg">
        {snippets[active]}
      </pre>
      <div className="flex justify-end mt-2">
        <Button size="sm" variant="ghost" onClick={copy}>
          {copied ? "Copied!" : "📋 Copy"}
        </Button>
      </div>
    </div>
  );
}
