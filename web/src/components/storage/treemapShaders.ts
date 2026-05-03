/**
 * GLSL ES 3.0 shaders for the WebGL2 treemap renderer.
 *
 * The vertex shader expands a static unit quad over per-instance bounds
 * (x, y, w, h in pixels) and converts to clip space via u_viewport. The
 * fragment shader fills with v_fill, drawing an inset stroke band of
 * width v_strokeWidth in v_stroke whenever the fragment's distance from
 * the nearest rect edge is below the stroke threshold.
 *
 * Stride per instance: vec4 bounds + vec4 fill + vec4 stroke + float
 * strokeWidth = 13 floats = 52 bytes. At 50k instances that's 2.6 MB of
 * GPU buffer data — comfortably within budget. One drawArraysInstanced
 * call paints the entire treemap; mouse-move triggers no redraws (only
 * hover-node changes do).
 */

export const VERT_SRC = `#version 300 es
in vec2 a_quad;
in vec4 a_bounds;
in vec4 a_fill;
in vec4 a_stroke;
in float a_strokeWidth;

uniform vec2 u_viewport;
// v0.4.11 Phase 9 — pan + scale. rendered = (instance_px * scale) + translate.
// IDENTITY = (translate=(0,0), scale=1) → no transform.
uniform vec2 u_translate;
uniform float u_scale;

out vec4 v_fill;
out vec4 v_stroke;
out float v_strokeWidth;
out vec2 v_localPx;
out vec2 v_size;

void main() {
  vec2 px = a_bounds.xy + a_quad * a_bounds.zw;
  vec2 transformed = (px * u_scale) + u_translate;
  vec2 clip = (transformed / u_viewport) * 2.0 - 1.0;
  clip.y = -clip.y;
  gl_Position = vec4(clip, 0.0, 1.0);

  v_fill = a_fill;
  v_stroke = a_stroke;
  // Stroke widens with zoom (e.g., 2px @ scale=2 -> 4px on screen).
  // Pre-multiplying here avoids a divide in the fragment shader.
  v_strokeWidth = a_strokeWidth;
  v_localPx = a_quad * a_bounds.zw;
  v_size = a_bounds.zw;
}
`;

export const FRAG_SRC = `#version 300 es
precision mediump float;

in vec4 v_fill;
in vec4 v_stroke;
in float v_strokeWidth;
in vec2 v_localPx;
in vec2 v_size;

out vec4 fragColor;

void main() {
  float dx = min(v_localPx.x, v_size.x - v_localPx.x);
  float dy = min(v_localPx.y, v_size.y - v_localPx.y);
  float d = min(dx, dy);
  if (d < v_strokeWidth && v_stroke.a > 0.0) {
    fragColor = v_stroke;
  } else {
    fragColor = v_fill;
  }
}
`;
