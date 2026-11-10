/**
 * Pilihan batch di halaman admin. Batch 1 = admin lama (hasil scan v1, FastAPI di /admin),
 * Batch 2 = profiling FIF (aplikasi ini). Tautan batch 1 sengaja <a> biasa ke /admin (di luar basePath /ljk).
 */
export default function BatchSwitch({ active }: { active: 1 | 2 }) {
  const opt = (n: 1 | 2, label: string, sub: string, href: string) => (
    <a
      href={href}
      aria-checked={active === n}
      role="radio"
      className={`flex flex-1 items-center gap-3 rounded-xl border-2 px-4 py-3 transition ${
        active === n ? "border-brand bg-brand-soft" : "border-line bg-white hover:border-ink3"
      }`}
    >
      <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 ${active === n ? "border-brand" : "border-ink3"}`}>
        {active === n && <span className="h-2 w-2 rounded-full bg-brand" />}
      </span>
      <span className="leading-tight">
        <span className="block text-sm font-bold">{label}</span>
        <span className="block text-xs text-ink2">{sub}</span>
      </span>
    </a>
  );
  return (
    <div role="radiogroup" aria-label="Pilih batch" className="flex flex-col gap-2 sm:flex-row">
      {opt(1, "Batch 1", "Hasil scan sebelumnya (admin lama)", "/admin")}
      {opt(2, "Batch 2", "Profiling FIF", "/ljk/admin")}
    </div>
  );
}
