"use server";

import { revalidatePath } from "next/cache";
import { getUjianAktif, requireAdmin } from "@/lib/auth";
import { parseKunciRows } from "@/lib/omr/grade";

export async function tambahPengawas(formData: FormData) {
  const { supabase } = await requireAdmin();
  const lines = String(formData.get("daftar") ?? "")
    .split(/\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  const role = formData.get("role") === "admin" ? "admin" : "pengawas";
  const rows = lines
    .map((l) => {
      // format: email[, nama]  atau  nama <email>
      const m = l.match(/[\w.+-]+@[\w-]+(\.[\w-]+)+/);
      if (!m) return null;
      const email = m[0].toLowerCase();
      const nama = l.replace(m[0], "").replace(/[<>,;\t]/g, " ").trim() || email.split("@")[0];
      return { email, nama, role, aktif: true };
    })
    .filter((r): r is NonNullable<typeof r> => r !== null);
  if (rows.length) await supabase.from("pengawas").upsert(rows, { onConflict: "email" });
  revalidatePath("/admin");
}

export async function setPengawas(formData: FormData) {
  const { supabase, pengawas } = await requireAdmin();
  const email = String(formData.get("email"));
  const action = String(formData.get("action"));
  if (email === pengawas.email && action !== "promote") return; // jangan kunci diri sendiri
  if (action === "toggle") {
    const aktif = formData.get("aktif") === "true";
    await supabase.from("pengawas").update({ aktif: !aktif }).eq("email", email);
  } else if (action === "promote") {
    await supabase.from("pengawas").update({ role: "admin" }).eq("email", email);
  } else if (action === "demote") {
    await supabase.from("pengawas").update({ role: "pengawas" }).eq("email", email);
  }
  revalidatePath("/admin");
}

export async function simpanKunci(formData: FormData) {
  const { supabase } = await requireAdmin();
  const ujian = await getUjianAktif(supabase);
  if (!ujian) return;
  const kode = String(formData.get("kode"));
  const text = String(formData.get("kunci") ?? "");
  const rows = text
    .split(/\n/)
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => l.split(/[\s,;\t.)]+/).filter(Boolean));
  const parsed = parseKunciRows(rows);
  const jawaban = Object.fromEntries(Object.entries(parsed).map(([k, v]) => [String(k), v]));
  if (Object.keys(jawaban).length === 0) return;
  await supabase.from("kunci").upsert({ ujian_id: ujian.id, kode_soal: kode, jawaban, updated_at: new Date().toISOString() });
  revalidatePath("/admin");
}
