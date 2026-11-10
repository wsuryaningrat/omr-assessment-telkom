import type { NextConfig } from "next";

/**
 * Integrasi ke mathcenter.cswda.id (lihat INTEGRASI.md):
 * - NEXT_PUBLIC_BASE_PATH  : isi "/ljk" bila aplikasi dipasang di mathcenter.cswda.id/ljk (reverse proxy).
 *                            Kosongkan bila memakai subdomain sendiri / domain vercel.app.
 * - NEXT_PUBLIC_SITE_URL   : origin publik, mis. https://mathcenter.cswda.id (dipakai untuk redirect login).
 * - FRAME_ANCESTORS        : origin yang boleh menyematkan aplikasi dalam iframe (default: situs Math Center).
 */
const basePath = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/$/, "");
const frameAncestors = process.env.FRAME_ANCESTORS ?? "https://mathcenter.cswda.id https://*.cswda.id";

const nextConfig: NextConfig = {
  // standalone: server mandiri kecil untuk container di VPS (Dockerfile); Vercel tetap bisa membangunnya.
  output: "standalone",
  basePath: basePath || undefined,
  serverExternalPackages: ["exceljs"],
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          // Kamera hanya untuk aplikasi ini (dan frame yang diberi allow="camera").
          { key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=()" },
          { key: "Content-Security-Policy", value: `frame-ancestors 'self' ${frameAncestors}` },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
