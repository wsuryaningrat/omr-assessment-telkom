import { redirect } from "next/navigation";
import { supabaseServer } from "@/lib/supabase/server";

export interface Pengawas {
  email: string;
  nama: string;
  role: "pengawas" | "admin";
  aktif: boolean;
}

/** Pastikan user login DAN terdaftar di whitelist pengawas. */
export async function requirePengawas() {
  const supabase = await supabaseServer();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");
  const email = (user.email ?? "").toLowerCase();
  const { data: pengawas } = await supabase.from("pengawas").select("email,nama,role,aktif").eq("email", email).maybeSingle();
  if (!pengawas || !pengawas.aktif) redirect("/auth/tidak-terdaftar");
  return { supabase, user, pengawas: pengawas as Pengawas };
}

export async function requireAdmin() {
  const ctx = await requirePengawas();
  if (ctx.pengawas.role !== "admin") redirect("/");
  return ctx;
}

/** Ujian aktif terbaru. */
export async function getUjianAktif(supabase: Awaited<ReturnType<typeof supabaseServer>>) {
  const { data } = await supabase
    .from("ujian")
    .select("id,nama,template_version,kode_soal")
    .eq("aktif", true)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  return data as { id: string; nama: string; template_version: string; kode_soal: string[] | null } | null;
}
