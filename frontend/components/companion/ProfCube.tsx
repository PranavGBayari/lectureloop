"use client";
// Prof. Cube, the student's study partner. Renders the 3D voxel figure (React Three Fiber) when
// WebGL is available, otherwise a static 2D drawing of the same figure. The 3D scene is code-split
// (next/dynamic, client only), so three.js never blocks the page; the drawing shows while it loads.
/* eslint-disable @next/next/no-img-element -- static SVG fallback */
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import type { AvatarState } from "./avatarState";

export type { AvatarState } from "./avatarState";

function Still({ label }: { label: string }) {
  return (
    <img
      src="/companion/prof-cube.svg"
      alt={label}
      width={160}
      height={175}
      className="mx-auto block h-full w-auto max-w-full object-contain"
    />
  );
}

const Scene = dynamic(() => import("./ProfCubeScene"), { ssr: false, loading: () => <Still label="" /> });

function hasWebGL() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

const LABEL: Record<AvatarState, string> = {
  idle: "Prof. Cube, listening",
  thinking: "Prof. Cube is thinking about your answer",
  happy: "Prof. Cube is celebrating",
  nudge: "Prof. Cube is puzzling it over",
  explaining: "Prof. Cube is explaining with chalk",
};

export function ProfCube({ state, className = "" }: { state: AvatarState; className?: string }) {
  const [gl, setGl] = useState<boolean | null>(null); // null until checked on the client
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    setGl(hasWebGL());
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  return (
    <div role="img" aria-label={LABEL[state]} data-avatar-state={state} className={`relative ${className}`}>
      {gl ? (
        <Scene state={state} reduced={reduced} fallback={<Still label={LABEL[state]} />} />
      ) : (
        <Still label={gl === false ? LABEL[state] : ""} />
      )}
    </div>
  );
}
