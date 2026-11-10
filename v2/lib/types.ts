export type SubmissionStatus = "perlu_validasi" | "valid" | "ditolak" | "diganti";

export interface Ujian {
  id: string;
  nama: string;
  template_version: string;
  /** Daftar kode soal yang berlaku di ujian ini (tidak rahasia). */
  kode_soal: string[];
}

export interface Kelas {
  id: string;
  ujian_id: string;
  prodi: string | null;
  ruangan: string | null;
  hari: string | null;
  waktu: string | null;
  dosen?: string | null;
}

export interface Peserta {
  kelas_id: string;
  nim: string;
  nama: string;
  kelas_asal?: string | null;
  status?: string | null;
}

export interface Progres {
  kelas_id: string;
  prodi: string | null;
  ruangan: string | null;
  hari: string | null;
  waktu: string | null;
  total_peserta: number;
  terupload: number;
  tervalidasi: number;
  perlu_validasi: number;
  perlu_cek: number;
  terkirim_at?: string | null;
  terkirim_jumlah?: number | null;
}

export interface Submission {
  id: string;
  ujian_id: string;
  kelas_id: string;
  nim: string;
  nim_terbaca: string | null;
  nim_sumber: "omr" | "dropdown" | "manual" | "validator";
  kode_soal: string | null;
  jawaban: (string | null)[];
  kuisioner: (string | null)[];
  omr: StoredOmr;
  flags: number;
  photo_path: string;
  uploaded_by: string;
  uploaded_at: string;
  status: SubmissionStatus;
  validated_by: string | null;
  validated_at: string | null;
  catatan: string | null;
}

export interface StoredItem {
  value: string | null;
  status: "ok" | "blank" | "multi" | "ragu";
  scores: number[];
  conf: number;
}

/** Ringkasan hasil OMR yang disimpan di kolom `omr` (jsonb). */
export interface StoredOmr {
  template_version: string;
  image: { w: number; h: number };
  corners: { tl: { x: number; y: number }; tr: { x: number; y: number }; br: { x: number; y: number }; bl: { x: number; y: number } };
  quality: { pxPerMm: number; sharpness: number; minAngle: number; maxAngle: number; issues: string[] };
  stats: { baseline: number; peak: number; threshold: number };
  nim: StoredItem[];
  kode_soal: StoredItem;
  /** Detail per digit kode soal (LJK v2-2 ke atas). */
  kode_items?: StoredItem[];
  kuisioner: StoredItem[];
  soal: StoredItem[];
  /** Geseran lokal per nomor (mm) dari penyelarasan bubble — agar overlay pas dengan foto. */
  align?: Record<"nim" | "kode_soal" | "kuisioner" | "soal", [number, number][]>;
  ms: number;
  captured_at: string;
  user_agent?: string;
}

export const STATUS_LABEL: Record<SubmissionStatus, string> = {
  perlu_validasi: "Perlu validasi",
  valid: "Valid",
  ditolak: "Ditolak",
  diganti: "Diganti",
};

export const HARI_ORDER = ["SENIN", "SELASA", "RABU", "KAMIS", "JUMAT", "SABTU", "MINGGU"];
