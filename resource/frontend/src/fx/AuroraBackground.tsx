import { useEffect, useRef } from 'react';
import { FX_EVENTS, prefersReducedMotion } from './fxBus';

// Full-viewport WebGL aurora that lives behind the entire app shell. Domain-
// warped fbm noise drifts slowly in the brand palette (blue/violet/teal); the
// pointer parallaxes the field, and an `fx-flare` event surges it green for a
// few seconds (used by the analysis-complete celebration). Renders at half
// resolution (the field is soft by design), pauses while the tab is hidden,
// and degrades to a single static frame under prefers-reduced-motion or when
// WebGL is unavailable.

const VERT = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`;

const FRAG = `
precision highp float;
uniform vec2 uRes;
uniform float uTime;
uniform vec2 uMouse;
uniform float uFlare;
uniform float uEnergy;
uniform float uLight;

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
    mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x),
    u.y
  );
}

float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  mat2 r = mat2(0.8, 0.6, -0.6, 0.8);
  for (int i = 0; i < 5; i++) {
    v += a * noise(p);
    p = r * p * 2.03;
    a *= 0.55;
  }
  return v;
}

void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  vec2 p = vec2(uv.x * uRes.x / uRes.y, uv.y);
  float t = uTime * 0.045;
  vec2 drift = (uMouse - 0.5) * 0.22;

  vec2 q = vec2(
    fbm(p * 1.1 + vec2(t * 0.7, -t * 0.4)),
    fbm(p * 1.1 + vec2(-t * 0.5, t * 0.6) + 4.7)
  );
  float f = fbm(p * 1.4 + q * 1.8 + drift);
  float band = smoothstep(0.30, 0.95, f);
  float wisp = smoothstep(0.55, 0.95, fbm(p * 2.8 - q * 1.2 - vec2(0.0, t * 0.3)));

  vec3 cBlue   = vec3(0.15, 0.35, 0.95);
  vec3 cViolet = vec3(0.48, 0.25, 0.95);
  vec3 cTeal   = vec3(0.05, 0.65, 0.78);
  vec3 cGreen  = vec3(0.10, 0.85, 0.45);

  vec3 col = mix(cBlue, cViolet, clamp(q.x * 1.6 - 0.1, 0.0, 1.0));
  col = mix(col, cTeal, clamp(q.y * 1.4 - 0.2, 0.0, 1.0));
  col = mix(col, cGreen, clamp(uFlare, 0.0, 1.0) * 0.55);

  // Aurora lives mostly in the upper half, with a soft radial falloff.
  float vfade = smoothstep(-0.1, 0.75, uv.y) * 0.75 + 0.25;
  float vig = 1.0 - 0.45 * length(uv - vec2(0.5, 0.62));
  float glow = (band + wisp * 0.8 * band) * vfade * vig;
  float amp = 0.055 + uFlare * 0.16 + uEnergy * 0.05;

  vec3 dark = vec3(0.027, 0.029, 0.038);
  vec3 outDark = dark + col * glow * amp;

  vec3 lightBase = vec3(0.972, 0.973, 0.976);
  vec3 outLight = mix(lightBase, col, glow * (0.05 + uFlare * 0.06));

  gl_FragColor = vec4(mix(outDark, outLight, clamp(uLight, 0.0, 1.0)), 1.0);
}
`;

function compile(gl: WebGLRenderingContext, type: number, src: string): WebGLShader | null {
  const sh = gl.createShader(type);
  if (!sh) return null;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    gl.deleteShader(sh);
    return null;
  }
  return sh;
}

export function AuroraBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const gl = canvas.getContext('webgl', {
      alpha: false,
      antialias: false,
      depth: false,
      stencil: false,
      powerPreference: 'low-power',
    });
    if (!gl) return;

    const vs = compile(gl, gl.VERTEX_SHADER, VERT);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return;
    const prog = gl.createProgram();
    if (!prog) return;
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
    gl.useProgram(prog);

    // Fullscreen triangle.
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, 'aPos');
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    const uRes = gl.getUniformLocation(prog, 'uRes');
    const uTime = gl.getUniformLocation(prog, 'uTime');
    const uMouse = gl.getUniformLocation(prog, 'uMouse');
    const uFlare = gl.getUniformLocation(prog, 'uFlare');
    const uEnergy = gl.getUniformLocation(prog, 'uEnergy');
    const uLight = gl.getUniformLocation(prog, 'uLight');

    const reduced = prefersReducedMotion();
    let raf = 0;
    let running = false;
    let contextLost = false;
    const start = performance.now();
    let last = start;

    // Smoothed inputs.
    const mouse = { x: 0.5, y: 0.5, tx: 0.5, ty: 0.5 };
    let flare = 0;
    let flareTarget = 0;
    let energy = 0;
    let energyTarget = 0;
    let simTime = 0; // integrated in JS so energy can speed up drift without time jumps
    let light = document.documentElement.getAttribute('data-theme') === 'light' ? 1 : 0;
    let lightTarget = light;

    const resize = () => {
      // Half-res render: the field is intentionally soft, so upscaling is free
      // quality-wise and quarters the fragment workload.
      const scale = Math.min(window.devicePixelRatio || 1, 2) * 0.5;
      const w = Math.max(1, Math.round(canvas.clientWidth * scale));
      const h = Math.max(1, Math.round(canvas.clientHeight * scale));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        gl.viewport(0, 0, w, h);
      }
    };

    const draw = (timeSec: number) => {
      resize();
      gl.uniform2f(uRes, canvas.width, canvas.height);
      gl.uniform1f(uTime, timeSec);
      gl.uniform2f(uMouse, mouse.x, 1 - mouse.y);
      gl.uniform1f(uFlare, flare);
      gl.uniform1f(uEnergy, energy);
      gl.uniform1f(uLight, light);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };

    const frame = (now: number) => {
      raf = 0;
      if (!running || contextLost) return;
      const dt = Math.min((now - last) / 1000, 0.1);
      last = now;
      mouse.x += (mouse.tx - mouse.x) * Math.min(1, dt * 2.5);
      mouse.y += (mouse.ty - mouse.y) * Math.min(1, dt * 2.5);
      // Quick attack, slow release.
      flare += (flareTarget - flare) * Math.min(1, dt * (flareTarget > flare ? 10 : 0.55));
      flareTarget = Math.max(0, flareTarget - dt * 0.6);
      energy += (energyTarget - energy) * Math.min(1, dt * 1.5);
      light += (lightTarget - light) * Math.min(1, dt * 6);
      simTime += dt * (1 + energy * 0.9);
      draw(simTime);
      raf = requestAnimationFrame(frame);
    };

    const startLoop = () => {
      if (running || reduced || contextLost) return;
      running = true;
      last = performance.now();
      raf = requestAnimationFrame(frame);
    };
    const stopLoop = () => {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    };

    const onPointer = (e: PointerEvent) => {
      mouse.tx = e.clientX / window.innerWidth;
      mouse.ty = e.clientY / window.innerHeight;
    };
    const onFlare = () => {
      flareTarget = 1.25;
      if (reduced) draw(137);
    };
    const onEnergy = (e: Event) => {
      const level = (e as CustomEvent<{ level?: number }>).detail?.level;
      energyTarget = Math.max(0, Math.min(1, typeof level === 'number' ? level : 0));
    };
    const onVisibility = () => {
      if (document.hidden) stopLoop();
      else startLoop();
    };
    const onResize = () => {
      if (reduced && !contextLost) draw(137);
    };
    const onContextLost = (e: Event) => {
      e.preventDefault();
      contextLost = true;
      stopLoop();
    };

    const themeObserver = new MutationObserver(() => {
      lightTarget = document.documentElement.getAttribute('data-theme') === 'light' ? 1 : 0;
      if (reduced && !contextLost) {
        light = lightTarget;
        draw(137);
      }
    });
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });

    window.addEventListener('pointermove', onPointer, { passive: true });
    window.addEventListener(FX_EVENTS.flare, onFlare);
    window.addEventListener(FX_EVENTS.energy, onEnergy);
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('resize', onResize);
    canvas.addEventListener('webglcontextlost', onContextLost);

    if (reduced) {
      light = lightTarget;
      draw(137); // one pretty static frame
    } else {
      startLoop();
    }

    return () => {
      stopLoop();
      themeObserver.disconnect();
      window.removeEventListener('pointermove', onPointer);
      window.removeEventListener(FX_EVENTS.flare, onFlare);
      window.removeEventListener(FX_EVENTS.energy, onEnergy);
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('resize', onResize);
      canvas.removeEventListener('webglcontextlost', onContextLost);
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className="fx-aurora"
      style={{
        position: 'fixed',
        inset: 0,
        width: '100vw',
        height: '100vh',
        zIndex: -1,
        pointerEvents: 'none',
      }}
    />
  );
}
