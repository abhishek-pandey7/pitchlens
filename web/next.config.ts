import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The container image sets NEXT_OUTPUT=standalone for a small, self-contained server (web/Dockerfile);
  // local `next start` and the e2e suite use the default output.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
