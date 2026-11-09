import ExcelJS from "exceljs";
import { NextResponse } from "next/server";
import { getUjianAktif } from "@/lib/auth";
import { loadRekap } from "@/lib/rekap";
import { supabaseServer } from "@/lib/supabase/server";

export async function GET(request: Request) {
  const supabase = await supabaseServer();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const ujian = await getUjianAktif(supabase);
  if (!ujian) return NextResponse.json({ error: "tidak ada ujian aktif" }, { status: 404 });
  const format = new URL(request.url).searchParams.get("format") ?? "xlsx";
  let data;
  try {
    data = await loadRekap(supabase, ujian.id);
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 403 });
  }
  const { rows, belum } = data;
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
  const base = [
    "Kelas", "Prodi", "NIM", "Nama", "Kode Soal", "Benar", "Salah", "Kosong", "Total Soal", "Nilai",
    "Status", "Tanda", "Diupload oleh", "Waktu upload", "Divalidasi oleh", "Waktu validasi",
  ];
  const nSoal = Math.max(75, ...rows.map((r) => r.jawaban?.length ?? 0));
  const nKuis = Math.max(15, ...rows.map((r) => r.kuisioner?.length ?? 0));
  const header = [...base, ...Array.from({ length: nKuis }, (_, i) => `K${i + 1}`), ...Array.from({ length: nSoal }, (_, i) => `S${i + 1}`)];
  const toRow = (r: (typeof rows)[number]) => [
    r.kelas_id, r.prodi, r.nim, r.nama ?? "(di luar daftar)", r.kode_soal, r.benar, r.salah, r.kosong, r.total,
    r.nilai === null ? null : Number(r.nilai), r.status, r.flags, r.uploaded_by,
    r.uploaded_at ? new Date(r.uploaded_at) : null, r.validated_by, r.validated_at ? new Date(r.validated_at) : null,
    ...Array.from({ length: nKuis }, (_, i) => r.kuisioner?.[i] ?? ""),
    ...Array.from({ length: nSoal }, (_, i) => r.jawaban?.[i] ?? ""),
  ];

  if (format === "csv") {
    const esc = (v: unknown) => {
      if (v === null || v === undefined) return "";
      const s = v instanceof Date ? v.toISOString() : String(v);
      return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const csv = [header, ...rows.map(toRow)].map((r) => r.map(esc).join(",")).join("\n");
    return new NextResponse("﻿" + csv, {
      headers: {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": `attachment; filename="rekap_${ujian.id}_${stamp}.csv"`,
      },
    });
  }

  const wb = new ExcelJS.Workbook();
  wb.creator = "LJK Scanner v2 — Math Center";
  const ws = wb.addWorksheet("Rekap Nilai", { views: [{ state: "frozen", xSplit: 4, ySplit: 1 }] });
  ws.addRow(header);
  rows.forEach((r) => ws.addRow(toRow(r)));
  ws.getRow(1).font = { bold: true, color: { argb: "FFFFFFFF" } };
  ws.getRow(1).fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFBA0C2F" } };
  ws.columns.forEach((c, i) => (c.width = i < base.length ? [18, 10, 15, 32, 9, 7, 7, 7, 9, 8, 14, 7, 28, 18, 28, 18][i] : 4));
  ws.getColumn(14).numFmt = "yyyy-mm-dd hh:mm";
  ws.getColumn(16).numFmt = "yyyy-mm-dd hh:mm";
  ws.autoFilter = { from: { row: 1, column: 1 }, to: { row: 1, column: base.length } };

  const ws2 = wb.addWorksheet("Belum Upload");
  ws2.addRow(["Kelas", "Prodi", "NIM", "Nama"]);
  belum.forEach((b) => ws2.addRow([b.kelas_id, b.prodi, b.nim, b.nama]));
  ws2.getRow(1).font = { bold: true };
  ws2.columns = [{ width: 20 }, { width: 10 }, { width: 15 }, { width: 36 }];

  const ws3 = wb.addWorksheet("Ringkasan Kelas");
  ws3.addRow(["Kelas", "Prodi", "LJK", "Valid", "Perlu validasi", "Belum upload", "Rata-rata nilai"]);
  const kelasIds = [...new Set([...rows.map((r) => r.kelas_id), ...belum.map((b) => b.kelas_id)])].sort();
  for (const k of kelasIds) {
    const rr = rows.filter((r) => r.kelas_id === k);
    const nilai = rr.map((r) => (r.nilai === null ? null : Number(r.nilai))).filter((x): x is number => x !== null);
    ws3.addRow([
      k,
      rr[0]?.prodi ?? belum.find((b) => b.kelas_id === k)?.prodi ?? "",
      rr.length,
      rr.filter((r) => r.status === "valid").length,
      rr.filter((r) => r.status === "perlu_validasi").length,
      belum.filter((b) => b.kelas_id === k).length,
      nilai.length ? Math.round((nilai.reduce((a, b) => a + b, 0) / nilai.length) * 100) / 100 : null,
    ]);
  }
  ws3.getRow(1).font = { bold: true };
  ws3.columns = [{ width: 20 }, { width: 10 }, { width: 8 }, { width: 8 }, { width: 14 }, { width: 13 }, { width: 15 }];

  const buf = await wb.xlsx.writeBuffer();
  return new NextResponse(buf as ArrayBuffer, {
    headers: {
      "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "Content-Disposition": `attachment; filename="rekap_${ujian.id}_${stamp}.xlsx"`,
    },
  });
}
