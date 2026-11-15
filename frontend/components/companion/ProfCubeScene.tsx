"use client";
// Prof. Cube: a voxel lecturer built from 25 boxes (one shared geometry, one toon material per colour).
// Two layers of motion:
//   1. Every block is a little spring toward its rest spot: that gives the fly-in on load and the
//      "pop apart and snap back" on happy (an outward kick to each spring's velocity).
//   2. Joint groups (root, head, arms, mouth corners, eyes, thought dots) damp toward per-state
//      targets each frame, so changing `state` blends smoothly instead of snapping.
// Loaded with next/dynamic (ssr: false) from ProfCube.tsx, so three.js stays out of the main bundle.
import { Edges, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { AvatarState } from "./avatarState";

const C = {
  skin: "#e6eef5", // head and hands
  mint: "#5eead4", // torso, upper arms
  legs: "#a5b4fc",
  cap: "#15253a",
  capEdge: "#8ba3b8", // the cap matches the card background, so it gets an outline
  ink: "#0e1a24", // eyes, mouth
  chalk: "#ffffff",
  tassel: "#f5c26b",
} as const;

const { damp } = THREE.MathUtils;
const rand = (a: number, b: number) => a + Math.random() * (b - a);

// ------------------------------------------------------------------ shared resources

type Shared = {
  box: THREE.BoxGeometry;
  mat: (color: string) => THREE.MeshToonMaterial;
  pop: React.RefObject<number>; // bumped on "happy": every block kicks outward once
  reduced: boolean; // prefers-reduced-motion: no fly-in, no pop, no auto-rotate
};
const SharedCtx = createContext<Shared | null>(null);

function useShared(reduced: boolean): Shared {
  const pop = useRef(0);
  return useMemo(() => {
    // 3-step ramp for crisp, flat toon bands.
    const ramp = new THREE.DataTexture(new Uint8Array([110, 190, 255]), 3, 1, THREE.RedFormat);
    ramp.minFilter = ramp.magFilter = THREE.NearestFilter;
    ramp.needsUpdate = true;
    const cache = new Map<string, THREE.MeshToonMaterial>();
    return {
      box: new THREE.BoxGeometry(1, 1, 1),
      mat: (color: string) => {
        let m = cache.get(color);
        if (!m) cache.set(color, (m = new THREE.MeshToonMaterial({ color, gradientMap: ramp })));
        return m;
      },
      pop,
      reduced,
    };
  }, [reduced]);
}

// ------------------------------------------------------------------ a spring-loaded block

type V3 = [number, number, number];
const K = 170; // spring stiffness
const DAMPING = 13; // ~0.5 damping ratio: a visible overshoot, settles in ~0.6 s
const STAGGER = 0.026; // seconds between blocks; 25 blocks -> ~0.65 s + settle ≈ 1.2 s

function Block({ i, at, size, color, outline }: { i: number; at: V3; size: V3; color: string; outline?: string }) {
  const ctx = useContext(SharedCtx)!;
  const ref = useRef<THREE.Mesh>(null);
  const s = useMemo(() => {
    const far = !ctx.reduced;
    return {
      p: at.map((v) => v + (far ? rand(-3.2, 3.2) : 0)) as V3,
      v: [0, 0, 0] as V3,
      r: far ? ([rand(-3, 3), rand(-3, 3), rand(-3, 3)] as V3) : ([0, 0, 0] as V3),
      delay: far ? i * STAGGER : 0,
      seenPop: ctx.pop.current,
    };
    // Rest position is fixed per block; deliberately computed once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useFrame(({ clock }, delta) => {
    const m = ref.current;
    if (!m) return;
    const dt = Math.min(delta, 1 / 30);
    const live = clock.elapsedTime >= s.delay;
    m.visible = live;
    if (!live) return;
    if (ctx.pop.current !== s.seenPop) {
      s.seenPop = ctx.pop.current;
      if (!ctx.reduced) {
        // Kick outward from the block's rest spot (plus jitter), the spring pulls it back.
        const len = Math.hypot(at[0], at[1] - 0.6, at[2]) || 1;
        s.v[0] += (at[0] / len) * 4 + rand(-1, 1);
        s.v[1] += ((at[1] - 0.6) / len) * 4 + rand(0, 1.5);
        s.v[2] += (at[2] / len) * 4 + rand(-1, 1);
        s.r = [rand(-0.6, 0.6), rand(-0.6, 0.6), rand(-0.6, 0.6)];
      }
    }
    for (let k = 0; k < 3; k++) {
      s.v[k] += (K * (at[k] - s.p[k]) - DAMPING * s.v[k]) * dt;
      s.p[k] += s.v[k] * dt;
      s.r[k] = damp(s.r[k], 0, 7, dt);
    }
    m.position.set(s.p[0], s.p[1], s.p[2]);
    m.rotation.set(s.r[0], s.r[1], s.r[2]);
  });

  return (
    <mesh ref={ref} geometry={ctx.box} material={ctx.mat(color)} scale={size} visible={false}>
      {outline && <Edges color={outline} />}
    </mesh>
  );
}

// ------------------------------------------------------------------ the figure

// Rig dimensions (world units; feet on y = 0).
const SHOULDER_Y = 2.05;
const ARM_X = 0.79;
const NECK_Y = 2.23;
const HEAD = 1.25;

type Pose = {
  rootY: number;
  armL: V3; // rotation (x, y, z) of the left-arm pivot (character's right, viewer's left)
  armR: V3; // the chalk arm
  head: V3;
  smile: number; // mouth corner lift; 0 = flat line
  dots: number; // thought-dot scale
};

function poseFor(state: AvatarState, t: number, idleTilt: number): Pose {
  switch (state) {
    case "thinking":
      return {
        rootY: Math.sin(t * 1.6) * 0.03,
        armL: [0, 0, -0.1],
        armR: [-1.75, 0, -0.7], // hand tucked under the chin
        head: [-0.14, 0.32 + Math.sin(t * 0.9) * 0.08, 0.1],
        smile: 0.02,
        dots: 1,
      };
    case "happy":
      return {
        rootY: Math.abs(Math.sin(t * 7)) * 0.45,
        armL: [0, 0, -2.6 + Math.sin(t * 14) * 0.12],
        armR: [0, 0, 2.6 - Math.sin(t * 14) * 0.12],
        head: [-0.12, 0, Math.sin(t * 7) * 0.06],
        smile: 0.09,
        dots: 0,
      };
    case "nudge":
      return {
        rootY: Math.sin(t * 1.2) * 0.02,
        armL: [0.1, 0, -3.0 + Math.sin(t * 16) * 0.1], // scratching the side of the head
        armR: [0, 0, 0.12],
        head: [0.05, -0.12, 0.26],
        smile: 0,
        dots: 0,
      };
    case "explaining":
      return {
        rootY: Math.sin(t * 2) * 0.03,
        armL: [0, 0, -0.15],
        armR: [-1.45, 0, 0.08], // chalk pointed at the viewer
        head: [0.1 + Math.sin(t * 5) * 0.12, 0.08, 0],
        smile: 0.07,
        dots: 0,
      };
    default:
      return {
        rootY: Math.sin(t * 2) * 0.05,
        armL: [0, 0, -0.08 - Math.sin(t * 2) * 0.03],
        armR: [0, 0, 0.08 + Math.sin(t * 2) * 0.03],
        head: [0, 0, idleTilt],
        smile: 0.06,
        dots: 0,
      };
  }
}

function dampV(o: THREE.Euler, to: V3, lambda: number, dt: number) {
  o.x = damp(o.x, to[0], lambda, dt);
  o.y = damp(o.y, to[1], lambda, dt);
  o.z = damp(o.z, to[2], lambda, dt);
}

function Figure({ state }: { state: AvatarState }) {
  const ctx = useContext(SharedCtx)!;
  const root = useRef<THREE.Group>(null);
  const head = useRef<THREE.Group>(null);
  const armL = useRef<THREE.Group>(null);
  const armR = useRef<THREE.Group>(null);
  const cornerL = useRef<THREE.Group>(null);
  const cornerR = useRef<THREE.Group>(null);
  const eyes = useRef<THREE.Group>(null);
  const dots = useRef<THREE.Group>(null);
  const tassel = useRef<THREE.Group>(null);
  const stateRef = useRef(state);
  const life = useRef({ nextBlink: 2, blinkUntil: 0, nextTilt: 3, tilt: 0 });

  useEffect(() => {
    if (state === "happy" && stateRef.current !== "happy") ctx.pop.current += 1;
    stateRef.current = state;
  }, [state, ctx]);

  useFrame(({ clock }, delta) => {
    const t = clock.elapsedTime;
    const dt = Math.min(delta, 1 / 30);
    const L = life.current;
    // Idle life: a blink every few seconds, an occasional curious head tilt.
    if (t > L.nextBlink) {
      L.blinkUntil = t + 0.12;
      L.nextBlink = t + rand(2.5, 5.5);
    }
    if (t > L.nextTilt) {
      L.tilt = [0, 0.13, -0.11, 0][Math.floor(Math.random() * 4)];
      L.nextTilt = t + rand(2.5, 4.5);
    }
    const p = poseFor(stateRef.current, ctx.reduced ? 0 : t, L.tilt);
    const lam = 7; // blend speed between poses

    if (root.current) root.current.position.y = damp(root.current.position.y, p.rootY, 12, dt);
    if (head.current) dampV(head.current.rotation, p.head, lam, dt);
    if (armL.current) dampV(armL.current.rotation, p.armL, lam, dt);
    if (armR.current) dampV(armR.current.rotation, p.armR, lam, dt);
    for (const c of [cornerL.current, cornerR.current]) if (c) c.position.y = damp(c.position.y, p.smile, 10, dt);
    if (eyes.current) eyes.current.scale.y = damp(eyes.current.scale.y, t < L.blinkUntil ? 0.1 : 1, 40, dt);
    if (dots.current) {
      const sc = damp(dots.current.scale.x, p.dots, 8, dt);
      dots.current.scale.setScalar(sc);
      dots.current.visible = sc > 0.01;
      dots.current.rotation.y += dt * 2.2;
      dots.current.position.y = HEAD + 0.45 + Math.sin(t * 3) * 0.05;
    }
    if (tassel.current) tassel.current.rotation.z = Math.sin(t * 2.6) * 0.18;
  });

  let i = 0; // fly-in order: bottom up
  const n = () => i++;
  return (
    <group ref={root}>
      {/* Legs: two blocks each, with gaps. */}
      {[-0.28, 0.28].map((x) => (
        <group key={x}>
          <Block i={n()} at={[x, 0.21, 0]} size={[0.42, 0.42, 0.42]} color={C.legs} />
          <Block i={n()} at={[x, 0.69, 0]} size={[0.42, 0.42, 0.42]} color={C.legs} />
        </group>
      ))}
      {/* Torso: two stacked boxes. */}
      <Block i={n()} at={[0, 1.235, 0]} size={[1.0, 0.55, 0.6]} color={C.mint} />
      <Block i={n()} at={[0, 1.87, 0]} size={[1.1, 0.6, 0.65]} color={C.mint} />
      {/* Arms pivot at the shoulder: upper block + hand block. */}
      <group ref={armL} position={[-ARM_X, SHOULDER_Y, 0]}>
        <Block i={n()} at={[0, -0.2, 0]} size={[0.36, 0.36, 0.36]} color={C.mint} />
        <Block i={n()} at={[0, -0.62, 0]} size={[0.36, 0.36, 0.36]} color={C.skin} />
      </group>
      <group ref={armR} position={[ARM_X, SHOULDER_Y, 0]}>
        <Block i={n()} at={[0, -0.2, 0]} size={[0.36, 0.36, 0.36]} color={C.mint} />
        <Block i={n()} at={[0, -0.62, 0]} size={[0.36, 0.36, 0.36]} color={C.skin} />
        <Block i={n()} at={[0, -0.95, 0.04]} size={[0.08, 0.42, 0.08]} color={C.chalk} />
      </group>
      {/* Head pivots at the neck. */}
      <group ref={head} position={[0, NECK_Y, 0]}>
        <Block i={n()} at={[0, HEAD / 2, 0]} size={[HEAD, HEAD, HEAD]} color={C.skin} />
        <group ref={eyes} position={[0, 0.72, HEAD / 2 + 0.02]}>
          <Block i={n()} at={[-0.25, 0, 0]} size={[0.16, 0.2, 0.04]} color={C.ink} />
          <Block i={n()} at={[0.25, 0, 0]} size={[0.16, 0.2, 0.04]} color={C.ink} />
        </group>
        <group position={[0, 0.38, HEAD / 2 + 0.02]}>
          <Block i={n()} at={[0, 0, 0]} size={[0.3, 0.07, 0.04]} color={C.ink} />
          <group ref={cornerL} position={[0, 0.06, 0]}>
            <Block i={n()} at={[-0.19, 0, 0]} size={[0.08, 0.07, 0.04]} color={C.ink} />
          </group>
          <group ref={cornerR} position={[0, 0.06, 0]}>
            <Block i={n()} at={[0.19, 0, 0]} size={[0.08, 0.07, 0.04]} color={C.ink} />
          </group>
        </group>
        {/* Graduation cap: board + button, tassel of three tiny cubes. */}
        <Block i={n()} at={[0, HEAD + 0.04, 0]} size={[1.5, 0.08, 1.5]} color={C.cap} outline={C.capEdge} />
        <Block i={n()} at={[0, HEAD + 0.18, 0]} size={[0.2, 0.2, 0.2]} color={C.cap} outline={C.capEdge} />
        <group ref={tassel} position={[0.72, HEAD + 0.02, 0.2]}>
          <Block i={n()} at={[0, -0.07, 0]} size={[0.09, 0.09, 0.09]} color={C.tassel} />
          <Block i={n()} at={[0, -0.18, 0]} size={[0.09, 0.09, 0.09]} color={C.tassel} />
          <Block i={n()} at={[0, -0.29, 0]} size={[0.09, 0.09, 0.09]} color={C.tassel} />
        </group>
        {/* "..." thought dots orbiting above the head. */}
        <group ref={dots} scale={0} visible={false}>
          {[0, 1, 2].map((k) => (
            <Block
              key={k}
              i={n()}
              at={[Math.cos((k * 2 * Math.PI) / 3) * 0.6, k * 0.06, Math.sin((k * 2 * Math.PI) / 3) * 0.6]}
              size={[0.14, 0.14, 0.14]}
              color={C.mint}
            />
          ))}
        </group>
      </group>
    </group>
  );
}

// ------------------------------------------------------------------ camera behaviour

/** Auto-rotate slowly while idle and untouched; otherwise ease back to face the student. */
function Controls({ state, reduced }: { state: AvatarState; reduced: boolean }) {
  const ref = useRef<OrbitControlsImpl>(null);
  const touch = useRef({ active: false, justEnded: false, endedAt: -10 });
  useFrame(({ clock }, delta) => {
    const c = ref.current;
    if (!c) return;
    if (touch.current.justEnded) {
      touch.current.justEnded = false;
      touch.current.endedAt = clock.elapsedTime;
    }
    const calm = !touch.current.active && clock.elapsedTime - touch.current.endedAt > 2.5;
    c.autoRotate = calm && state === "idle" && !reduced;
    if (calm && state !== "idle") {
      c.setAzimuthalAngle(damp(c.getAzimuthalAngle(), 0, 3, Math.min(delta, 1 / 30)));
    }
  });
  return (
    <OrbitControls
      ref={ref}
      target={[0, 1.85, 0]}
      enableZoom={false}
      enablePan={false}
      enableDamping
      autoRotateSpeed={0.8}
      minPolarAngle={Math.PI / 3}
      maxPolarAngle={Math.PI / 1.8}
      onStart={() => (touch.current.active = true)}
      onEnd={() => {
        touch.current.active = false;
        touch.current.justEnded = true;
      }}
    />
  );
}

// ------------------------------------------------------------------ canvas

export default function ProfCubeScene({
  state,
  reduced,
  fallback,
}: {
  state: AvatarState;
  reduced: boolean;
  fallback: ReactNode;
}) {
  return (
    <Canvas
      flat // no tone mapping: keep the palette colours exact
      fallback={fallback}
      dpr={[1, 2]}
      gl={{ alpha: true, antialias: true, powerPreference: "low-power" }}
      camera={{ position: [0, 2.3, 8.2], fov: 34 }}
      style={{ background: "transparent" }}
    >
      <ambientLight intensity={0.95} />
      <directionalLight position={[3, 6, 5]} intensity={1.6} />
      <Scene state={state} reduced={reduced} />
    </Canvas>
  );
}

function Scene({ state, reduced }: { state: AvatarState; reduced: boolean }) {
  const shared = useShared(reduced);
  return (
    <SharedCtx.Provider value={shared}>
      <Figure state={state} />
      <Controls state={state} reduced={reduced} />
    </SharedCtx.Provider>
  );
}
