import LoginButton from "./LoginButton";

export default async function LoginPage(props: { searchParams: Promise<{ next?: string; error?: string }> }) {
  const { next, error } = await props.searchParams;
  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <div className="card w-full max-w-sm p-8 text-center">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-brand text-lg font-black text-white">
          MC
        </div>
        <h1 className="text-xl font-bold">LJK Scanner v2</h1>
        <p className="mt-1 text-sm text-muted">Math Center CSWDA · Telkom University</p>
        <p className="mt-6 text-sm text-slate-600">
          Masuk dengan akun Google yang sudah didaftarkan sebagai pengawas. Setiap foto LJK yang diunggah akan tercatat atas nama akun ini.
        </p>
        {error && <p className="mt-4 rounded-lg bg-red-50 p-2 text-sm text-bad">Login gagal: {error}</p>}
        <div className="mt-6">
          <LoginButton next={next} />
        </div>
        <p className="mt-6 text-xs text-muted">
          <a className="underline" href="/privasi">Kebijakan Privasi</a> · <a className="underline" href="/ketentuan">Ketentuan Layanan</a>
        </p>
      </div>
    </main>
  );
}
