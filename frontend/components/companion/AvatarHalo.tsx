// Soft teal radial glow behind the companion (Figma "Faint avatar halo" export).
/* eslint-disable @next/next/no-img-element -- static decorative SVG */
export function AvatarHalo() {
  return <img src="/companion/halo.svg" alt="" width={324} height={324} className="block max-w-none" aria-hidden />;
}
