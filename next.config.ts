import type { NextConfig } from "next";
const config: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.CUTLIST_WORKER_URL || "http://127.0.0.1:8000"}/api/:path*` }];
  },
  experimental: { cpus: 2, workerThreads: true, webpackBuildWorker: false, useTypeScriptCli: false },
};
export default config;
