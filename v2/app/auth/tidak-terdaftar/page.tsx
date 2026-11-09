import { supabaseServer } from "@/lib/supabase/server";

export default async function TidakTerdaftar() {
  const supabase = await supabaseServer();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <div className="card w-full max-w-md p-8 text-center">
        <h1 className="text-lg font-bold">Akun belum terdaftar</h1>
        <p className="mt-3 text-sm text-slate-600">
          Akun <b>{user?.email ?? "-"}</b> belum ada di daftar pengawas. Minta admin Math Center menambahkan Gmail ini, lalu masuk lagi.
        </p>
        <form action="/auth/signout" method="post" className="mt-6">
          <button className="btn-ghost w-full">Keluar & ganti akun</button>
        </form>
      </div>
    </main>
  );
}
