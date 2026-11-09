import Link from "next/link";

export const metadata = { title: "Kebijakan Privasi · LJK Scanner v2" };

export default function PrivasiPage() {
  return (
    <main className="mx-auto max-w-2xl p-6 py-12 text-sm leading-relaxed text-slate-700">
      <h1 className="text-2xl font-bold text-slate-900">Kebijakan Privasi</h1>
      <p className="mt-1 text-muted">LJK Scanner v2 · Math Center CSWDA · Telkom University</p>

      <h2 className="mt-8 font-semibold text-slate-900">Siapa yang memakai aplikasi ini</h2>
      <p className="mt-2">
        LJK Scanner v2 adalah alat internal Math Center CSWDA Telkom University untuk memindai lembar jawaban komputer (LJK).
        Hanya pengawas dan admin yang emailnya sudah didaftarkan yang bisa masuk.
      </p>

      <h2 className="mt-6 font-semibold text-slate-900">Data dari akun Google</h2>
      <p className="mt-2">
        Saat masuk dengan Google, aplikasi hanya menerima nama, alamat email, dan foto profil akun. Email dipakai untuk
        memeriksa apakah akun terdaftar sebagai pengawas dan untuk mencatat siapa yang mengunggah atau memvalidasi setiap LJK.
        Aplikasi tidak membaca Gmail, Drive, kontak, atau data Google lainnya.
      </p>

      <h2 className="mt-6 font-semibold text-slate-900">Data ujian</h2>
      <p className="mt-2">
        Aplikasi menyimpan foto LJK, hasil pembacaan jawaban, NIM, nilai, serta catatan siapa dan kapan data diunggah atau
        diubah. Data disimpan di Supabase dengan akses terbatas untuk pengawas dan admin terdaftar, dan dipakai hanya untuk
        penilaian dan analisis profiling Math Center.
      </p>

      <h2 className="mt-6 font-semibold text-slate-900">Berbagi data</h2>
      <p className="mt-2">Data tidak dijual dan tidak dibagikan ke pihak luar Telkom University.</p>

      <h2 className="mt-6 font-semibold text-slate-900">Kontak</h2>
      <p className="mt-2">
        Pertanyaan atau permintaan penghapusan data: <a className="underline" href="mailto:wahyusuryaningrat@gmail.com">wahyusuryaningrat@gmail.com</a>.
      </p>

      <p className="mt-10">
        <Link className="underline" href="/ketentuan">Ketentuan Layanan</Link> · <Link className="underline" href="/login">Masuk</Link>
      </p>
    </main>
  );
}
