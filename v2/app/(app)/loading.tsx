/** Tampil seketika saat pindah menu, sambil data halaman dimuat. */
export default function Loading() {
  return (
    <div className="animate-pulse space-y-4" aria-busy="true" aria-label="Memuat">
      <div className="h-28 rounded-[22px] bg-soft" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-20 rounded-[14px] bg-soft" />
        ))}
      </div>
      <div className="space-y-2 rounded-[14px] border border-line bg-white p-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-10 rounded-lg bg-soft" />
        ))}
      </div>
    </div>
  );
}
