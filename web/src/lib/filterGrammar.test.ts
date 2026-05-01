import { describe, expect, it } from "vitest";

import { Predicate, deserialize, sameTarget, serialize } from "./filterGrammar";

describe("filterGrammar serialize/deserialize", () => {
  it("round-trips a single extension predicate", () => {
    const preds: Predicate[] = [{ kind: "extension", value: "pdf" }];
    expect(deserialize(serialize(preds))).toEqual(preds);
  });

  it("round-trips a principal predicate with explicit right", () => {
    const preds: Predicate[] = [
      { kind: "principal", value: "sid:S-1-5-21-1234567890-987654321-1001", right: "write" },
    ];
    expect(deserialize(serialize(preds))).toEqual(preds);
  });

  it("round-trips path-like values with colons and slashes", () => {
    const preds: Predicate[] = [
      { kind: "owner", value: "DOMAIN\\alice/bob" },
      { kind: "source", value: "11111111-2222-3333-4444-555555555555" },
    ];
    expect(deserialize(serialize(preds))).toEqual(preds);
  });

  it("round-trips unicode values", () => {
    const preds: Predicate[] = [{ kind: "owner", value: "üser-ñame-🏠" }];
    expect(deserialize(serialize(preds))).toEqual(preds);
  });

  it("serializes an empty list to an empty string", () => {
    expect(serialize([])).toBe("");
    expect(deserialize("")).toEqual([]);
    expect(deserialize(null)).toEqual([]);
    expect(deserialize(undefined)).toEqual([]);
  });

  it("returns [] for malformed input rather than throwing", () => {
    expect(deserialize("not-base64!")).toEqual([]);
    // Valid base64 of "hello" — not a predicate list.
    expect(deserialize("aGVsbG8")).toEqual([]);
    // Valid base64 of `{"kind":"unknown","value":"x"}` — known structure
    // but unknown kind is filtered out.
    expect(deserialize("eyJraW5kIjoidW5rbm93biIsInZhbHVlIjoieCJ9")).toEqual([]);
  });

  it("drops unknown predicate kinds but keeps the known ones", () => {
    // Mixed list: one good, one bad. Bad one is dropped.
    const mixed = JSON.stringify([
      { kind: "extension", value: "pdf" },
      { kind: "garbage", value: "x" },
    ]);
    const encoded = btoa(mixed)
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    expect(deserialize(encoded)).toEqual([{ kind: "extension", value: "pdf" }]);
  });

  it("survives a size predicate with all three ops", () => {
    const preds: Predicate[] = [
      { kind: "size", op: "gte", value: 1024 },
      { kind: "size", op: "lte", value: 1048576 },
      { kind: "size", op: "eq", value: 0 },
    ];
    expect(deserialize(serialize(preds))).toEqual(preds);
  });

  it("preserves predicate order", () => {
    const preds: Predicate[] = [
      { kind: "extension", value: "pdf" },
      { kind: "size", op: "gte", value: 1024 },
      { kind: "owner", value: "alice" },
    ];
    expect(deserialize(serialize(preds))).toEqual(preds);
  });
});

describe("sameTarget", () => {
  it("matches identical extension predicates", () => {
    expect(
      sameTarget(
        { kind: "extension", value: "pdf" },
        { kind: "extension", value: "pdf" },
      ),
    ).toBe(true);
  });

  it("does not match different kinds", () => {
    expect(
      sameTarget(
        { kind: "extension", value: "pdf" },
        { kind: "owner", value: "pdf" },
      ),
    ).toBe(false);
  });

  it("matches principal predicates with same value+right (default read)", () => {
    expect(
      sameTarget(
        { kind: "principal", value: "sid:S-1-5" },
        { kind: "principal", value: "sid:S-1-5", right: "read" },
      ),
    ).toBe(true);
  });

  it("does not match principal predicates with different rights", () => {
    expect(
      sameTarget(
        { kind: "principal", value: "sid:S-1-5", right: "read" },
        { kind: "principal", value: "sid:S-1-5", right: "write" },
      ),
    ).toBe(false);
  });

  it("matches size predicates only when op is the same", () => {
    expect(
      sameTarget(
        { kind: "size", op: "gte", value: 100 },
        { kind: "size", op: "gte", value: 999 },
      ),
    ).toBe(true);
    expect(
      sameTarget(
        { kind: "size", op: "gte", value: 100 },
        { kind: "size", op: "lte", value: 100 },
      ),
    ).toBe(false);
  });
});
