import { NextResponse } from "next/server";
import { publicUrl } from "@/lib/basePath";
import { supabaseServer } from "@/lib/supabase/server";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const code = searchParams.get("code");
  const nextParam = searchParams.get("next") ?? "/";
  const next = nextParam.startsWith("/") && !nextParam.startsWith("//") ? nextParam : "/";
  if (!code) return NextResponse.redirect(publicUrl(request, "/login?error=kode_tidak_ada"));

  const supabase = await supabaseServer();
  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (!error) return NextResponse.redirect(publicUrl(request, next));

  // Callback bisa terpanggil dua kali (mis. permintaan diulang browser/proxy). Kode OAuth hanya
  // bisa ditukar sekali: bila permintaan pertama sudah membuat sesi, lanjutkan saja.
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (user) return NextResponse.redirect(publicUrl(request, next));

  const msg = /flow state|code verifier|expired/i.test(error.message)
    ? "Sesi login kedaluwarsa atau sudah dipakai. Silakan klik Masuk dengan Google sekali lagi."
    : error.message;
  return NextResponse.redirect(publicUrl(request, `/login?error=${encodeURIComponent(msg)}&next=${encodeURIComponent(next)}`));
}
