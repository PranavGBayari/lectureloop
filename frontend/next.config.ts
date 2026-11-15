import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The repo root also has a package.json (for `npm run dev`); pin Turbopack to this app.
  turbopack: { root: path.resolve(__dirname) },
};

export default nextConfig;
