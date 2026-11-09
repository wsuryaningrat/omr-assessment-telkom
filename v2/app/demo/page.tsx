import { notFound } from "next/navigation";
import ScanFlow from "@/components/scan/ScanFlow";

/**
 * Mode demo untuk mencoba kamera + OMR tanpa login/database.
 * Aktif HANYA jika env NEXT_PUBLIC_DEMO_MODE=1 (matikan di produksi).
 */
export default function DemoPage() {
  if (process.env.NEXT_PUBLIC_DEMO_MODE !== "1") notFound();
  const peserta = Array.from({ length: 12 }, (_, i) => ({
    kelas_id: "DEMO-01",
    nim: `1030526000${String(i + 1).padStart(2, "0")}`,
    nama: ["Ani Lestari", "Budi Santoso", "Citra Dewi", "Dimas Pratama", "Eka Putri", "Fajar Nugroho", "Gita Maharani", "Hadi Wijaya", "Intan Sari", "Joko Susilo", "Kirana Ayu", "Lukman Hakim"][i],
  }));
  return (
    <main className="mx-auto max-w-4xl px-4 py-5">
      <ScanFlow
        demo
        kelas={{ id: "DEMO-01", ujian_id: "demo", prodi: "S1 Demo", ruangan: "-", hari: "-", waktu: "-" }}
        peserta={peserta}
        existing={[]}
        pengawasEmail="demo@local"
      />
    </main>
  );
}
