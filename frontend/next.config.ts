import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root: an unrelated lockfile in a parent directory otherwise
  // makes Turbopack infer the wrong root and warn on every build.
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
