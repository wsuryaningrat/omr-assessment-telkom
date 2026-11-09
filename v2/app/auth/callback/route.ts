import { NextResponse } from "next/server";
import { publicUrl } from "@/lib/basePath";
import { supabaseServer } from "@/lib/supabase/server";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const code = searchParams.get("code");
  const nextParam = searchParams.get("next") ?? "/";
  const next = nextParam.startsWith("/") && !nextParam.startsWith("//") ? nextParam : "/";
  if (code) {
    const supabase = await supabaseServer();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(publicUrl(request, next));
    return NextResponse.redirect(publicUrl(request, `/login?error=${encodeURIComponent(error.message)}`));
  }
  return NextResponse.redirect(publicUrl(request, "/login?error=kode_tidak_ada"));
}
