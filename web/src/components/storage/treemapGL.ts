/**
 * WebGL2 instanced renderer for the treemap.
 *
 * Replaces the SVG-per-node renderer (which emitted 5000+ DOM elements
 * and re-rendered the entire tree on every mouse move). One
 * drawArraysInstanced call paints all rects; mouse-move triggers no
 * redraws — only hover-node identity changes do. Headroom for 50k+
 * rects at 60 fps.
 */
import { FRAG_SRC, VERT_SRC } from "./treemapShaders";

export type Rgba = [number, number, number, number];

export interface RenderInstance {
  /** pixel-space rect: top-left + size */
  x: number;
  y: number;
  w: number;
  h: number;
  fill: Rgba;
  stroke: Rgba;
  /** 0 to disable stroke; otherwise pixel width of the inset stroke band */
  strokeWidth: number;
}

export interface GLRenderer {
  /** Resize the backing canvas. Pass CSS pixel dims; renderer handles
   *  internal sizing. Call this whenever the container size changes. */
  resize(widthCss: number, heightCss: number): void;
  /** Paint the treemap. Single instanced draw call. */
  draw(instances: readonly RenderInstance[]): void;
  /** Free WebGL resources. Call on unmount. */
  dispose(): void;
}

const FLOATS_PER_INSTANCE = 13; // vec4 + vec4 + vec4 + float
const STRIDE_BYTES = FLOATS_PER_INSTANCE * 4;

/**
 * Parse "#rrggbb", "#rgb", "rgb(r, g, b)", or "rgba(r, g, b, a)" into
 * normalized [r, g, b, a] ∈ [0, 1]. Returns transparent black on
 * unparsable input rather than throwing — the renderer is best-effort
 * and we'd rather miss a fill than crash a treemap on a typo.
 */
export function parseColor(str: string): Rgba {
  const s = str.trim();
  if (s.startsWith("#")) {
    const hex = s.slice(1);
    const expanded =
      hex.length === 3
        ? hex
            .split("")
            .map((c) => c + c)
            .join("")
        : hex;
    if (expanded.length !== 6) return [0, 0, 0, 0];
    const n = parseInt(expanded, 16);
    if (isNaN(n)) return [0, 0, 0, 0];
    return [
      ((n >> 16) & 255) / 255,
      ((n >> 8) & 255) / 255,
      (n & 255) / 255,
      1,
    ];
  }
  // rgb()/rgba() — comma-separated, optional alpha.
  const m = s.match(/^rgba?\s*\(\s*([^)]+)\)$/i);
  if (!m) return [0, 0, 0, 0];
  const parts = m[1].split(",").map((p) => p.trim());
  if (parts.length < 3) return [0, 0, 0, 0];
  const r = parseFloat(parts[0]) / 255;
  const g = parseFloat(parts[1]) / 255;
  const b = parseFloat(parts[2]) / 255;
  const a = parts.length >= 4 ? parseFloat(parts[3]) : 1;
  return [r, g, b, a];
}

function compileShader(
  gl: WebGL2RenderingContext,
  type: number,
  src: string,
): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, src);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    // eslint-disable-next-line no-console
    console.error("treemapGL shader compile failed:", gl.getShaderInfoLog(shader));
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

function compileProgram(
  gl: WebGL2RenderingContext,
  vertSrc: string,
  fragSrc: string,
): WebGLProgram | null {
  const vert = compileShader(gl, gl.VERTEX_SHADER, vertSrc);
  const frag = compileShader(gl, gl.FRAGMENT_SHADER, fragSrc);
  if (!vert || !frag) return null;
  const program = gl.createProgram();
  if (!program) return null;
  gl.attachShader(program, vert);
  gl.attachShader(program, frag);
  gl.linkProgram(program);
  // Shaders can be deleted as soon as they're attached + linked.
  gl.deleteShader(vert);
  gl.deleteShader(frag);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    // eslint-disable-next-line no-console
    console.error("treemapGL program link failed:", gl.getProgramInfoLog(program));
    gl.deleteProgram(program);
    return null;
  }
  return program;
}

/**
 * Build a renderer bound to the given canvas. Returns null if WebGL2
 * isn't available — caller should fall back to a non-rendered placeholder.
 */
export function createGLRenderer(canvas: HTMLCanvasElement): GLRenderer | null {
  const gl = canvas.getContext("webgl2", {
    antialias: true,
    premultipliedAlpha: false,
  });
  if (!gl) return null;

  const program = compileProgram(gl, VERT_SRC, FRAG_SRC);
  if (!program) return null;

  // Static unit quad; triangle strip — 4 verts cover the rect.
  const quadBuf = gl.createBuffer();
  const instBuf = gl.createBuffer();
  const vao = gl.createVertexArray();
  if (!quadBuf || !instBuf || !vao) {
    gl.deleteProgram(program);
    return null;
  }

  const quad = new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]);
  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
  gl.bufferData(gl.ARRAY_BUFFER, quad, gl.STATIC_DRAW);

  const aQuad = gl.getAttribLocation(program, "a_quad");
  const aBounds = gl.getAttribLocation(program, "a_bounds");
  const aFill = gl.getAttribLocation(program, "a_fill");
  const aStroke = gl.getAttribLocation(program, "a_stroke");
  const aStrokeW = gl.getAttribLocation(program, "a_strokeWidth");
  const uViewport = gl.getUniformLocation(program, "u_viewport");

  gl.bindVertexArray(vao);

  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
  gl.enableVertexAttribArray(aQuad);
  gl.vertexAttribPointer(aQuad, 2, gl.FLOAT, false, 0, 0);
  gl.vertexAttribDivisor(aQuad, 0);

  gl.bindBuffer(gl.ARRAY_BUFFER, instBuf);
  gl.enableVertexAttribArray(aBounds);
  gl.vertexAttribPointer(aBounds, 4, gl.FLOAT, false, STRIDE_BYTES, 0);
  gl.vertexAttribDivisor(aBounds, 1);
  gl.enableVertexAttribArray(aFill);
  gl.vertexAttribPointer(aFill, 4, gl.FLOAT, false, STRIDE_BYTES, 16);
  gl.vertexAttribDivisor(aFill, 1);
  gl.enableVertexAttribArray(aStroke);
  gl.vertexAttribPointer(aStroke, 4, gl.FLOAT, false, STRIDE_BYTES, 32);
  gl.vertexAttribDivisor(aStroke, 1);
  gl.enableVertexAttribArray(aStrokeW);
  gl.vertexAttribPointer(aStrokeW, 1, gl.FLOAT, false, STRIDE_BYTES, 48);
  gl.vertexAttribDivisor(aStrokeW, 1);

  gl.bindVertexArray(null);
  gl.bindBuffer(gl.ARRAY_BUFFER, null);

  let widthPx = 0;
  let heightPx = 0;
  let instCapacity = 0;
  let scratch = new Float32Array(0);

  function uploadInstances(instances: readonly RenderInstance[]): number {
    const n = instances.length;
    if (scratch.length < n * FLOATS_PER_INSTANCE) {
      // Grow with headroom so we don't realloc on every frame.
      scratch = new Float32Array(n * FLOATS_PER_INSTANCE * 2);
    }
    for (let i = 0; i < n; i++) {
      const ins = instances[i];
      const o = i * FLOATS_PER_INSTANCE;
      scratch[o] = ins.x;
      scratch[o + 1] = ins.y;
      scratch[o + 2] = ins.w;
      scratch[o + 3] = ins.h;
      scratch[o + 4] = ins.fill[0];
      scratch[o + 5] = ins.fill[1];
      scratch[o + 6] = ins.fill[2];
      scratch[o + 7] = ins.fill[3];
      scratch[o + 8] = ins.stroke[0];
      scratch[o + 9] = ins.stroke[1];
      scratch[o + 10] = ins.stroke[2];
      scratch[o + 11] = ins.stroke[3];
      scratch[o + 12] = ins.strokeWidth;
    }
    gl!.bindBuffer(gl!.ARRAY_BUFFER, instBuf);
    if (n > instCapacity) {
      // Resize buffer with the same headroom strategy.
      gl!.bufferData(
        gl!.ARRAY_BUFFER,
        scratch.byteLength,
        gl!.DYNAMIC_DRAW,
      );
      instCapacity = scratch.length / FLOATS_PER_INSTANCE;
    }
    gl!.bufferSubData(
      gl!.ARRAY_BUFFER,
      0,
      scratch,
      0,
      n * FLOATS_PER_INSTANCE,
    );
    gl!.bindBuffer(gl!.ARRAY_BUFFER, null);
    return n;
  }

  return {
    resize(widthCss, heightCss) {
      // Render at device pixel resolution for crisp edges on hi-dpi
      // displays. The CSS sizing keeps layout / pointer events in CSS
      // pixels — only the canvas's drawing buffer scales.
      const dpr = window.devicePixelRatio || 1;
      widthPx = Math.max(1, Math.round(widthCss * dpr));
      heightPx = Math.max(1, Math.round(heightCss * dpr));
      canvas.width = widthPx;
      canvas.height = heightPx;
      canvas.style.width = `${widthCss}px`;
      canvas.style.height = `${heightCss}px`;
    },
    draw(instances) {
      if (widthPx === 0 || heightPx === 0) return;
      const dpr = window.devicePixelRatio || 1;
      gl.viewport(0, 0, widthPx, heightPx);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);

      gl.useProgram(program);
      gl.bindVertexArray(vao);
      // Convert layout pixel space (CSS px from d3) to device pixels
      // by feeding the device-pixel viewport into the shader. The
      // instance bounds are in CSS px; the shader uses them as-is and
      // the viewport ratio handles the dpr scale.
      gl.uniform2f(uViewport, widthPx / dpr, heightPx / dpr);

      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

      const n = uploadInstances(instances);
      if (n > 0) {
        gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, n);
      }

      gl.bindVertexArray(null);
    },
    dispose() {
      gl.deleteProgram(program);
      gl.deleteBuffer(quadBuf);
      gl.deleteBuffer(instBuf);
      gl.deleteVertexArray(vao);
      // Drop scratch ref so GC can reclaim.
      scratch = new Float32Array(0);
    },
  };
}
