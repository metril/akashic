import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { createGLRenderer, parseColor, type RenderInstance } from "./treemapGL";

describe("parseColor", () => {
  it("parses 6-digit hex", () => {
    const c = parseColor("#ff8000");
    expect(c[0]).toBeCloseTo(1);
    expect(c[1]).toBeCloseTo(128 / 255);
    expect(c[2]).toBeCloseTo(0);
    expect(c[3]).toBe(1);
  });

  it("parses 3-digit hex (expands)", () => {
    const c = parseColor("#f80");
    expect(c[0]).toBeCloseTo(255 / 255);
    expect(c[1]).toBeCloseTo(136 / 255);
    expect(c[2]).toBeCloseTo(0);
  });

  it("parses rgb()", () => {
    const c = parseColor("rgb(10, 20, 30)");
    expect(c[0]).toBeCloseTo(10 / 255);
    expect(c[1]).toBeCloseTo(20 / 255);
    expect(c[2]).toBeCloseTo(30 / 255);
    expect(c[3]).toBe(1);
  });

  it("parses rgba()", () => {
    const c = parseColor("rgba(10, 20, 30, 0.5)");
    expect(c[3]).toBeCloseTo(0.5);
  });

  it("returns transparent on garbage input", () => {
    expect(parseColor("not a color")).toEqual([0, 0, 0, 0]);
    expect(parseColor("#zzzz")).toEqual([0, 0, 0, 0]);
    expect(parseColor("rgb(broken")).toEqual([0, 0, 0, 0]);
  });

  it("ignores whitespace", () => {
    const c = parseColor("  rgb( 10 , 20 , 30 )  ");
    expect(c[0]).toBeCloseTo(10 / 255);
  });
});

// v0.4.14 Phase 2 — identity-cached upload. The renderer should skip
// bufferSubData when the same instances array reference is passed
// twice in a row (pan-only frames keep the scene stable; only the
// viewport uniform actually changes).
describe("createGLRenderer identity cache", () => {
  // vitest config runs in a node env (no jsdom). The renderer reads
  // window.devicePixelRatio for DPR-aware sizing; stub it.
  beforeAll(() => {
    (globalThis as unknown as { window: { devicePixelRatio: number } }).window =
      { devicePixelRatio: 1 };
  });
  afterAll(() => {
    delete (globalThis as unknown as { window?: unknown }).window;
  });

  function makeMockGL() {
    const calls: { method: string; args: unknown[] }[] = [];
    const constants: Record<string, number> = {
      ARRAY_BUFFER: 1, COMPILE_STATUS: 2, LINK_STATUS: 3, FLOAT: 4,
      STATIC_DRAW: 5, DYNAMIC_DRAW: 6, BLEND: 7, SRC_ALPHA: 8,
      ONE_MINUS_SRC_ALPHA: 9, COLOR_BUFFER_BIT: 10, TRIANGLE_STRIP: 11,
      VERTEX_SHADER: 12, FRAGMENT_SHADER: 13,
    };
    // Symbol-typed sentinels for the renderer's program/shader/buffer
    // handles. Each createX returns a fresh object so equality checks
    // pass and deletion calls don't clobber other state.
    const handler: ProxyHandler<object> = {
      get(_target, prop: string) {
        if (prop in constants) return constants[prop];
        if (prop === "getShaderParameter") return () => true;
        if (prop === "getProgramParameter") return () => true;
        if (prop === "createShader") return () => ({});
        if (prop === "createProgram") return () => ({});
        if (prop === "createBuffer") return () => ({});
        if (prop === "createVertexArray") return () => ({});
        if (prop === "getAttribLocation") return () => 0;
        if (prop === "getUniformLocation") return () => ({});
        return (...args: unknown[]) => {
          calls.push({ method: prop, args });
        };
      },
    };
    return { gl: new Proxy({}, handler), calls };
  }

  function makeMockCanvas(gl: object): HTMLCanvasElement {
    return {
      width: 0,
      height: 0,
      style: {} as CSSStyleDeclaration,
      getContext: () => gl,
    } as unknown as HTMLCanvasElement;
  }

  function makeInstances(n: number): RenderInstance[] {
    return Array.from({ length: n }, (_, i) => ({
      x: i, y: i, w: 10, h: 10,
      fill: [1, 0, 0, 1] as [number, number, number, number],
      stroke: [0, 0, 0, 1] as [number, number, number, number],
      strokeWidth: 1,
    }));
  }

  it("skips bufferSubData when the same instance array is drawn twice", () => {
    const { gl, calls } = makeMockGL();
    const canvas = makeMockCanvas(gl);
    const renderer = createGLRenderer(canvas);
    expect(renderer).not.toBeNull();
    renderer!.resize(100, 100);

    const instances = makeInstances(50);
    renderer!.draw(instances);
    const firstCount = calls.filter((c) => c.method === "bufferSubData").length;
    expect(firstCount).toBe(1);

    renderer!.draw(instances);  // same identity — should NOT re-upload
    const secondCount = calls.filter((c) => c.method === "bufferSubData").length;
    expect(secondCount).toBe(1);

    const newInstances = makeInstances(50);
    renderer!.draw(newInstances);  // new identity — must re-upload
    const thirdCount = calls.filter((c) => c.method === "bufferSubData").length;
    expect(thirdCount).toBe(2);

    renderer!.dispose();
  });

  it("still issues drawArraysInstanced on the cached frame", () => {
    const { gl, calls } = makeMockGL();
    const canvas = makeMockCanvas(gl);
    const renderer = createGLRenderer(canvas);
    renderer!.resize(100, 100);

    const instances = makeInstances(20);
    renderer!.draw(instances);
    renderer!.draw(instances);
    renderer!.draw(instances);

    const drawCount = calls.filter((c) => c.method === "drawArraysInstanced").length;
    expect(drawCount).toBe(3);  // every draw still paints, just doesn't re-upload

    renderer!.dispose();
  });
});
