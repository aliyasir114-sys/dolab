import type { NextConfig } from "next";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/v1";
const apiOrigin = new URL(apiUrl).origin;
const dev = process.env.NODE_ENV !== "production";

// A strict policy except for inline scripts, which Next.js needs for hydration. A nonce-based
// policy (no 'unsafe-inline') is a follow-up: it needs per-request rendering through a proxy file.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' blob: data: ${apiOrigin}`,
  `connect-src 'self' ${apiOrigin}`,
  "font-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // No image optimisation: photos are signed links from the API, shown as they are.
  images: { unoptimized: true },
  async redirects() {
    return [{ source: "/", destination: "/ar", permanent: false }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default config;
