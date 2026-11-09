import Link from "next/link";
import { asset } from "@/lib/basePath";
import LoginButton from "./LoginButton";

export default async function LoginPage(props: { searchParams: Promise<{ next?: string; error?: string }> }) {
  const { next, error } = await props.searchParams;
  return (
    <main className="flex min-h-dvh flex-col">
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-2.5">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={asset("/brand/mathcenter.png")} alt="Math Center" className="h-8 w-auto" />
          <div className="ml-auto flex items-center gap-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={asset("/brand/telkom.png")} alt="Telkom University" className="h-7 w-auto" />
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={asset("/brand/cswda.png")} alt="CSWDA" className="hidden h-6 w-auto sm:block" />
          </div>
        </div>
      </header>

      <div className="mx-auto grid w-full max-w-5xl flex-1 items-center gap-6 px-4 py-8 md:grid-cols-[1.15fr_1fr]">
        <section className="hero-band rounded-[22px] p-7 text-white md:p-9">
          <span className="inline-block rounded-full bg-white/12 px-3 py-1 text-[11px] font-semibold tracking-[0.12em] uppercase">
            Math Center · CSWDA Telkom University
          </span>
          <h1 className="mt-4 font-serif text-3xl leading-tight font-bold md:text-[40px]">
            Evaluasi <span className="text-[#f4b9a8]">LJK</span> dari kamera HP
          </h1>
          <p className="mt-3 max-w-md text-[15px] leading-relaxed text-white/80">
            Foto lembar jawaban satu per satu — sistem langsung membaca NIM, kode soal, dan jawaban, lalu menyimpannya untuk divalidasi.
          </p>
          <ul className="mt-5 space-y-1.5 text-sm text-white/85">
            <li>◉ Jepret otomatis saat 4 kotak hitam terlihat</li>
            <li>✓ Hasil baca langsung tampil, lanjut ke lembar berikutnya</li>
            <li>⟳ Upload berjalan di latar belakang</li>
          </ul>
        </section>

        <section className="card p-7">
          <h2 className="font-serif text-2xl font-bold">Masuk pengawas</h2>
          <p className="mt-2 text-sm leading-relaxed text-ink2">
            Gunakan akun Google yang sudah didaftarkan admin. Setiap LJK yang diunggah tercatat atas nama akun ini.
          </p>
          {error && <p className="mt-4 rounded-lg bg-bad-bg p-2.5 text-sm text-bad">Login gagal: {error}</p>}
          <div className="mt-6">
            <LoginButton next={next} />
          </div>
          <p className="mt-6 text-xs text-ink3">
            <Link className="underline" href="/privasi">
              Kebijakan Privasi
            </Link>{" "}
            ·{" "}
            <Link className="underline" href="/ketentuan">
              Ketentuan Layanan
            </Link>
          </p>
        </section>
      </div>
      <footer className="pb-6 text-center text-xs text-ink3">developed by DIS MathCenter CSWDA Telkom University</footer>
    </main>
  );
}
