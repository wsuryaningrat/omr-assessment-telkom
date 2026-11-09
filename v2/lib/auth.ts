import { cache } from "react";
import { redirect } from "next/navigation";
import { supabaseServer } from "@/lib/supabase/server";

export interface Pengawas {
  email: string;
  nama: string;
  role: "pengawas" | "admin";
  aktif: boolean;
}

/** Pastikan user login DAN terdaftar di whitelist pengawas. */
// cache(): layout + halaman dalam satu request hanya cek sekali.
// getClaims() memverifikasi JWT secara lokal (tanpa bolak-balik ke server Auth bila kunci asimetris).
export const requirePengawas = cache(async () => {
  const supabase = await supabaseServer();
  const { data } = await supabase.auth.getClaims();
  const claims = data?.claims;
  if (!claims) redirect("/login");
  const user = { id: claims.sub as string, email: (claims.email as string | undefined) ?? "" };
  const email = user.email.toLowerCase();
  const { data: pengawas } = await supabase.from("pengawas").select("email,nama,role,aktif").eq("email", email).maybeSingle();
  if (!pengawas || !pengawas.aktif) redirect("/auth/tidak-terdaftar");
  return { supabase, user, pengawas: pengawas as Pengawas };
});

export async function requireAdmin() {
  const ctx = await requirePengawas();
  if (ctx.pengawas.role !== "admin") redirect("/");
  return ctx;
}

/** Ujian aktif terbaru. */
export const getUjianAktif = cache(async (supabase: Awaited<ReturnType<typeof supabaseServer>>) => {
  const { data } = await supabase
    .from("ujian")
    .select("id,nama,template_version,kode_soal")
    .eq("aktif", true)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  return data as { id: string; nama: string; template_version: string; kode_soal: string[] | null } | null;
});
