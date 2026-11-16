import { describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", () => ({
  client: { GET: vi.fn(), POST: vi.fn() },
  ok: (x: unknown) => x,
}));

import { pollInterval } from "./DirectorySyncBar";

const status = (running: boolean[]) =>
  ({
    backends: running.map((r, i) => ({ backend: i ? "authentik" : "ad", configured: true, running: r, last_run: null, last_success_at: null })),
    interval_minutes: 60,
    next_run_at: null,
  }) as never;

describe("pollInterval", () => {
  it("polls every 2 s while any backend runs", () => {
    expect(pollInterval(status([false, true]))).toBe(2000);
  });
  it("stops when idle or without data", () => {
    expect(pollInterval(status([false, false]))).toBe(false);
    expect(pollInterval(undefined)).toBe(false);
  });
});
