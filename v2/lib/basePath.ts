/**
 * Prefix path saat aplikasi dipasang di bawah sub-path situs lain
 * (mis. mathcenter.cswda.id/ljk). Diisi lewat env NEXT_PUBLIC_BASE_PATH saat build.
 * <Link>, router, redirect() Next sudah otomatis memakai basePath; fungsi ini untuk
 * <a>/<img>/<form>/fetch/URL absolut yang tidak otomatis.
 */
export const BASE_PATH = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/$/, "");

export function asset(path: string): string {
  return `${BASE_PATH}${path}`;
}

/**
 * Origin publik untuk redirect di server. Di balik reverse proxy (mis. situs utama
 * meneruskan /ljk ke Vercel), host asli ada di x-forwarded-host. NEXT_PUBLIC_SITE_URL
 * (mis. https://mathcenter.cswda.id) mengalahkan keduanya bila diisi.
 */
export function publicOrigin(request: Request): string {
  const env = process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/$/, "");
  if (env) return env;
  const h = request.headers;
  const host = h.get("x-forwarded-host")?.split(",")[0].trim();
  if (host) return `${h.get("x-forwarded-proto")?.split(",")[0].trim() || "https"}://${host}`;
  return new URL(request.url).origin;
}

/** URL publik absolut untuk path aplikasi (sudah termasuk basePath). */
export function publicUrl(request: Request, path: string): string {
  return `${publicOrigin(request)}${BASE_PATH}${path}`;
}
