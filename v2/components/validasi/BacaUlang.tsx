"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { scanRgba } from "@/lib/omr/scan";
import { gabungBacaUlang } from "@/lib/omr/bacaUlang";
import { supabaseBrowser } from "@/lib/supabase/client";
import type { StoredOmr } from "@/lib/types";

type Row = {
  id: string;
  photo_path: string;
  flags: number;
  jawaban: (string | null)[];
  kuisioner: (string | null)[];
  omr: StoredOmr;
};

/**
 * Baca ulang foto LJK yang masih "ditandai" dengan engine OMR terbaru (di browser), lalu isi jawaban yang
 * dulu ragu/kosong tetapi sekarang terbaca yakin. Jawaban yang sudah terisi (hasil baca lama / koreksi
 * validator) TIDAK diubah. Opsional: LJK yang sudah bersih langsung ditandai valid.
 */
export default function BacaUlang({ kelasId, jumlah }: { kelasId: string; jumlah: number }) {
  const router = useRouter();
  const [auto, setAuto] = useState(true);
  const [run, setRun] = useState<null | { done: number; total: number; bersih: number; terisi: number; gagal: number }>(null);
  const [selesai, setSelesai] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const mulai = async () => {
    setErr(null);
    setSelesai(false);
    const supabase = supabaseBrowser();
    const { data, error } = await supabase
      .from("submissions")
      .select("id,photo_path,flags,jawaban,kuisioner,omr")
      .eq("kelas_id", kelasId)
      .eq("status", "perlu_validasi")
      .gt("flags", 0)
      .order("uploaded_at");
    if (error) return setErr(error.message);
    const rows = (data ?? []) as Row[];
    const st = { done: 0, total: rows.length, bersih: 0, terisi: 0, gagal: 0 };
    setRun({ ...st });
    for (const r of rows) {
      try {
        const dl = await supabase.storage.from("ljk").download(r.photo_path);
        if (dl.error || !dl.data) throw new Error(dl.error?.message ?? "foto tidak ada");
        const bmp = await createImageBitmap(dl.data);
        const canvas = document.createElement("canvas");
        canvas.width = bmp.width;
        canvas.height = bmp.height;
        const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
        ctx.drawImage(bmp, 0, 0);
        bmp.close();
        const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
        // beri jeda agar UI sempat memperbarui progres
        await new Promise((res) => setTimeout(res, 0));
        const scan = scanRgba(img.data, canvas.width, canvas.height);
        if (!scan.ok || !scan.decoded) throw new Error(scan.reason ?? "gagal membaca");
        const { jawaban, kuisioner, flags, terisi: filled } = gabungBacaUlang(r, scan.decoded);
        const jadiValid = auto && flags === 0;
        if (filled > 0 || flags !== r.flags || jadiValid) {
          const { error: upErr } = await supabase
            .from("submissions")
            .update({ jawaban, kuisioner, flags, ...(jadiValid ? { status: "valid" } : {}) })
            .eq("id", r.id);
          if (upErr) throw new Error(upErr.message);
        }
        st.terisi += filled;
        if (flags === 0) st.bersih++;
      } catch {
        st.gagal++;
      }
      st.done++;
      setRun({ ...st });
    }
    setSelesai(true);
    router.refresh();
  };

  if (jumlah === 0 && !run) return null;
  const busy = run !== null && !selesai;
  return (
    <div className="card space-y-2 p-4 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <b>Baca ulang LJK yang ditandai</b>
          <p className="text-xs text-muted">
            Foto dibaca lagi dengan pembaca terbaru (arsiran pensil tipis kini terbaca). Jawaban yang sudah terisi tidak diubah.
          </p>
        </div>
        <button type="button" onClick={mulai} disabled={busy} className="btn-primary px-4 py-2 text-sm disabled:opacity-50">
          {busy ? `Membaca ${run!.done}/${run!.total}…` : `↻ Baca ulang ${jumlah} LJK`}
        </button>
      </div>
      <label className="flex items-center gap-2 text-xs text-ink2">
        <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} disabled={busy} />
        Langsung tandai <b>valid</b> bila sudah tidak ada nomor yang ragu dan NIM/kode soal cocok
      </label>
      {run && (
        <p className={`text-xs ${selesai ? "text-ok" : "text-muted"}`}>
          {run.done}/{run.total} dibaca · {run.terisi} jawaban terisi · {run.bersih} LJK jadi bersih
          {auto ? " (ditandai valid)" : ""}
          {run.gagal > 0 && <span className="text-bad"> · {run.gagal} gagal dibaca (cek manual)</span>}
          {selesai && " · selesai"}
        </p>
      )}
      {err && <p className="text-xs text-bad">{err}</p>}
    </div>
  );
}
