import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE, ApiError, WS_BASE, client, clearToken, getToken, pendingCount, ok, setToken, parseApiError } from "./client";

describe("parseApiError", () => {
  it("reads the ErrorResponse envelope", () => {
    const e = parseApiError(404, { code: "not_found", message: "Source not found", detail: null, request_id: "abc" });
    expect(e).toBeInstanceOf(ApiError);
    expect(e.message).toBe("Source not found");
    expect(e.code).toBe("not_found");
    expect(e.status).toBe(404);
    expect(e.requestId).toBe("abc");
  });

  it("keeps structured validation detail", () => {
    const detail = [{ loc: ["body", "x"], msg: "bad", type: "value_error" }];
    const e = parseApiError(422, { code: "validation_error", message: "Request failed validation", detail, request_id: "r" });
    expect(e.detail).toEqual(detail);
  });

  it("does not fall back to the legacy {detail: string} shape", () => {
    const e = parseApiError(400, { detail: "old style" });
    expect(e.message).toBe("HTTP error 400");
    expect(e.code).toBe("http_400");
  });

  it("tolerates non-JSON bodies", () => {
    expect(parseApiError(502, null).message).toBe("HTTP error 502");
  });
});

describe("versioned base paths", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("pins the constants", () => {
    expect(API_BASE).toBe("/api/v1");
    expect(WS_BASE).toBe("/ws/v1");
  });

  it("client calls fetch under /api/v1", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json([]));
    vi.stubGlobal("fetch", fetchMock);
    await client.GET("/api/v1/sources");
    expect(new URL((fetchMock.mock.calls[0][0] as Request).url).pathname).toBe("/api/v1/sources");
  });
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("auth middleware", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    clearToken();
  });

  it("sends the bearer token", async () => {
    setToken("tok");
    const fetchMock = vi.fn().mockResolvedValue(json([]));
    vi.stubGlobal("fetch", fetchMock);
    await client.GET("/api/v1/sources");
    expect((fetchMock.mock.calls[0][0] as Request).headers.get("Authorization")).toBe("Bearer tok");
  });

  it("throws ApiError carrying the envelope on a non-2xx response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ code: "not_found", message: "nope", request_id: "r1" }, 404)),
    );
    await expect(
      client.GET("/api/v1/sources/{source_id}", { params: { path: { source_id: "x" } } }),
    ).rejects.toMatchObject({ status: 404, code: "not_found", requestId: "r1" });
  });

  it("does not leak replay state when the network fails", async () => {
    setToken("tok");
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("network down")).mockResolvedValue(json([]));
    vi.stubGlobal("fetch", fetchMock);
    await expect(client.GET("/api/v1/sources")).rejects.toThrow("network down");
    expect(pendingCount()).toBe(0);
  });

  it("refreshes once on 401 and replays the request with the new token", async () => {
    setToken("stale");
    const fetchMock = vi.fn(async (input: Request | string) => {
      // The refresh call is a plain fetch(url); everything else is a Request.
      if (typeof input === "string") return json({ access_token: "fresh", token_type: "bearer" });
      return input.headers.get("Authorization") === "Bearer fresh" ? json([{ id: "s" }]) : json({}, 401);
    });
    vi.stubGlobal("fetch", fetchMock);
    const data = ok(await client.GET("/api/v1/sources"));
    expect(data).toEqual([{ id: "s" }]);
    expect(getToken()).toBe("fresh");
    const refreshCalls = fetchMock.mock.calls.filter((c) => c[0] === "/api/v1/auth/refresh");
    expect(refreshCalls).toHaveLength(1);
  });
});
