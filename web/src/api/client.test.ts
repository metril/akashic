import { describe, expect, it } from "vitest";

import { ApiError, parseApiError } from "./client";

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
