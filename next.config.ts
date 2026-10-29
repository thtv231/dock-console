import type { NextConfig } from "next";

// In development Next proxies /api to uvicorn. On Vercel the Python function serves /api itself.
// 127.0.0.1, not localhost: uvicorn binds IPv4 and Windows resolves localhost to ::1.
// DOCK_API_URL overrides the target when port 8000 is taken.
const api = process.env.DOCK_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  devIndicators: false,   // the badge covers the theme toggle or the last row's Run again button
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default nextConfig;
