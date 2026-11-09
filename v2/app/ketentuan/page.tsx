import Link from "next/link";

export const metadata = { title: "Ketentuan Layanan · LJK Scanner v2" };

export default function KetentuanPage() {
  return (
    <main className="mx-auto max-w-2xl p-6 py-12 text-sm leading-relaxed text-slate-700">
      <h1 className="text-2xl font-bold text-slate-900">Ketentuan Layanan</h1>
      <p className="mt-1 text-muted">LJK Scanner v2 · Math Center CSWDA · Telkom University</p>
      <ol className="mt-8 list-decimal space-y-3 pl-5">
        <li>Aplikasi ini hanya untuk pengawas dan admin Math Center yang emailnya terdaftar.</li>
        <li>Pengguna wajib mengunggah foto LJK asli dari peserta ujian dan memeriksa NIM serta kode soal sebelum menyimpan.</li>
        <li>Setiap unggahan, perubahan, dan validasi tercatat atas nama akun yang masuk.</li>
        <li>Data peserta bersifat rahasia dan tidak boleh disebarkan di luar keperluan penilaian.</li>
        <li>Akses dapat dicabut oleh admin kapan saja.</li>
      </ol>
      <p className="mt-10">
        <Link className="underline" href="/privasi">Kebijakan Privasi</Link> · <Link className="underline" href="/login">Masuk</Link>
      </p>
    </main>
  );
}
