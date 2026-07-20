import { useEffect, useRef } from 'react';

/** Three brand-green planets on mutually perturbing 3D orbits around the bird.
 * This is a real N-body simulation, not a CSS ring: a fixed central mass (the
 * orb) plus pairwise planet-planet gravity, integrated with velocity Verlet at
 * a fixed timestep so the orbits precess and wobble as the planets tug on each
 * other. A tilted camera with a slow yaw projects the scene; each planet's
 * screen scale, opacity and stacking order follow its projected depth, so
 * bodies visibly pass behind and in front of the bird. The rAF loop mutates
 * the DOM directly — no per-frame React state. */

const GM_CENTER = 52000; // px³/s² — sets the tempo (inner orbit ≈ 5 s, outer ≈ 12 s)
const SOFTEN2 = 36; // softening (px²): caps close-pass forces so nothing slingshots
const R_MAX = 110; // stage boundary (px)
const CONTAIN = 2.5; // inward pull per px beyond R_MAX — inert in normal play
const DT = 1 / 120; // physics substep (s)
const MAX_FRAME = 0.1; // clamp after tab-restore so the sim never fast-forwards
const TILT = 1.05; // camera pitch (rad) — turns the orbital planes into ellipses
const YAW_RATE = (2 * Math.PI) / 48; // slow scene yaw exposes the third dimension
const FOCAL = 300; // perspective focal length (px)

/** Orbital elements per planet. `mass` is the planet's gravitational parameter
 * as a fraction of the central body's — big enough that the mutual tugs read
 * over a minute of watching, small enough that the system stays bound. */
const PLANETS = [
  { r: 32, incl: 0.5, node: 0.4, theta: 0.9, speed: 1.0, mass: 0.014, size: 7 },
  { r: 46, incl: 0.95, node: 2.3, theta: 3.6, speed: 0.97, mass: 0.02, size: 9 },
  { r: 58, incl: 0.68, node: 4.4, theta: 5.4, speed: 1.03, mass: 0.016, size: 8 },
];

type Vec3 = { x: number; y: number; z: number };
type Body = { p: Vec3; v: Vec3; gm: number };

/** Elements → state vector: enter a circle of radius r (× `speed` for slight
 * eccentricity) in the plane given by inclination + node, at phase theta. */
function bodyFromElements(el: (typeof PLANETS)[number]): Body {
  const cosN = Math.cos(el.node);
  const sinN = Math.sin(el.node);
  const cosI = Math.cos(el.incl);
  const sinI = Math.sin(el.incl);
  const u = { x: cosN, y: sinN, z: 0 };
  const w = { x: -sinN * cosI, y: cosN * cosI, z: sinI };
  const vc = el.speed * Math.sqrt(GM_CENTER / el.r);
  const ct = Math.cos(el.theta);
  const st = Math.sin(el.theta);
  return {
    p: {
      x: el.r * (ct * u.x + st * w.x),
      y: el.r * (ct * u.y + st * w.y),
      z: el.r * (ct * u.z + st * w.z),
    },
    v: {
      x: vc * (-st * u.x + ct * w.x),
      y: vc * (-st * u.y + ct * w.y),
      z: vc * (-st * u.z + ct * w.z),
    },
    gm: GM_CENTER * el.mass,
  };
}

function accelerations(bodies: Body[], out: Vec3[]) {
  for (let i = 0; i < bodies.length; i++) {
    const p = bodies[i].p;
    const r2 = p.x * p.x + p.y * p.y + p.z * p.z;
    const pull = -GM_CENTER / Math.pow(r2 + SOFTEN2, 1.5);
    let ax = p.x * pull;
    let ay = p.y * pull;
    let az = p.z * pull;
    for (let j = 0; j < bodies.length; j++) {
      if (j === i) continue;
      const q = bodies[j].p;
      const dx = q.x - p.x;
      const dy = q.y - p.y;
      const dz = q.z - p.z;
      const d2 = dx * dx + dy * dy + dz * dz;
      const f = bodies[j].gm / Math.pow(d2 + SOFTEN2, 1.5);
      ax += dx * f;
      ay += dy * f;
      az += dz * f;
    }
    const r = Math.sqrt(r2);
    if (r > R_MAX) {
      // Past the stage boundary a weak spring pulls the planet home — a rare
      // chaotic kick must not eject a body from an animation that runs forever.
      const k = (CONTAIN * (r - R_MAX)) / r;
      ax -= p.x * k;
      ay -= p.y * k;
      az -= p.z * k;
    }
    out[i].x = ax;
    out[i].y = ay;
    out[i].z = az;
  }
}

export function OrbPlanets() {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const els = Array.from(host.children) as HTMLElement[];
    const bodies = PLANETS.map(bodyFromElements);
    let acc: Vec3[] = bodies.map(() => ({ x: 0, y: 0, z: 0 }));
    let accNext: Vec3[] = bodies.map(() => ({ x: 0, y: 0, z: 0 }));
    accelerations(bodies, acc);
    let yaw = 0;
    let last: number | null = null;
    let raf = 0;

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (last === null) {
        last = now;
        return;
      }
      let elapsed = Math.min((now - last) / 1000, MAX_FRAME);
      last = now;
      yaw += YAW_RATE * elapsed;

      while (elapsed > 1e-6) {
        const dt = Math.min(DT, elapsed);
        elapsed -= dt;
        for (let i = 0; i < bodies.length; i++) {
          const b = bodies[i];
          const a = acc[i];
          b.p.x += b.v.x * dt + 0.5 * a.x * dt * dt;
          b.p.y += b.v.y * dt + 0.5 * a.y * dt * dt;
          b.p.z += b.v.z * dt + 0.5 * a.z * dt * dt;
        }
        accelerations(bodies, accNext);
        for (let i = 0; i < bodies.length; i++) {
          const b = bodies[i];
          b.v.x += 0.5 * (acc[i].x + accNext[i].x) * dt;
          b.v.y += 0.5 * (acc[i].y + accNext[i].y) * dt;
          b.v.z += 0.5 * (acc[i].z + accNext[i].z) * dt;
        }
        [acc, accNext] = [accNext, acc];
      }

      const cy = Math.cos(yaw);
      const sy = Math.sin(yaw);
      const ct = Math.cos(TILT);
      const st = Math.sin(TILT);
      for (let i = 0; i < bodies.length; i++) {
        const p = bodies[i].p;
        const x1 = p.x * cy + p.z * sy;
        const z1 = -p.x * sy + p.z * cy;
        const y2 = p.y * ct - z1 * st;
        const z2 = p.y * st + z1 * ct;
        const s = FOCAL / (FOCAL - z2);
        const el = els[i];
        el.style.transform = `translate(-50%, -50%) translate3d(${(x1 * s).toFixed(2)}px, ${(y2 * s).toFixed(2)}px, 0) scale(${s.toFixed(3)})`;
        el.style.opacity = (0.45 + 0.55 * Math.min(1, Math.max(0, (z2 + 70) / 140))).toFixed(3);
        el.style.zIndex = z2 < 0 ? '0' : '2';
      }
    };

    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div ref={hostRef} className="absolute inset-0 pointer-events-none" aria-hidden="true">
      {PLANETS.map((p) => (
        <span key={p.r} className="orb-planet" style={{ width: p.size, height: p.size }} />
      ))}
    </div>
  );
}
