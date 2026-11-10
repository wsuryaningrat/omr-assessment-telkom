export type Pt = { x: number; y: number };

/** Gambar grayscale 8-bit. */
export interface Gray {
  w: number;
  h: number;
  data: Uint8Array | Uint8ClampedArray;
}

export interface FieldItem {
  id: string;
  no?: number;
  /** Pusat bubble per opsi, dalam mm (origin kiri-atas halaman). */
  cells: [number, number][];
  label_xy?: [number, number];
}

export interface FieldDef {
  kind: "digits" | "single" | "mcq";
  label: string;
  options: string[];
  /** Jari-jari bubble (mm). */
  r: number;
  items: FieldItem[];
}

export interface Template {
  version: string;
  units: "mm";
  page: { w: number; h: number };
  marker: { size: number; centers: Record<"tl" | "tr" | "br" | "bl", [number, number]> };
  orientation: { x: number; y: number; w: number; h: number };
  /** Baris (y, mm) yang di template pasti kertas putih — untuk verifikasi kecocokan. */
  whiteRows?: number[];
  fields: Record<"nim" | "kode_soal" | "kuisioner" | "soal", FieldDef>;
}

export type Corners = { tl: Pt; tr: Pt; br: Pt; bl: Pt };

/** 3x3 homography row-major (h[8] = 1), memetakan mm template -> piksel gambar. */
export type Homography = number[];

export type ItemStatus = "ok" | "blank" | "multi" | "ragu";

export interface ItemResult {
  id: string;
  no?: number;
  /** Opsi terbaca (mis. "B" / "7" / "273"), null jika kosong / ganda. */
  value: string | null;
  status: ItemStatus;
  /** Skor kegelapan relatif per opsi (0..1, sudah dikurangi baseline kertas). */
  scores: number[];
  /** Keyakinan 0..1 (selisih opsi terkuat vs kedua). */
  conf: number;
}

export interface DecodedSheet {
  nim: { value: string; items: ItemResult[] };
  /** Kode soal gabungan (mis. "273"); status = digit terburuk. */
  kode_soal: ItemResult;
  /** Detail per digit kode soal. */
  kode_items: ItemResult[];
  kuisioner: ItemResult[];
  soal: ItemResult[];
  stats: { baseline: number; peak: number; threshold: number; alignAnchors?: number; alignRms?: number };
}

export interface QualityReport {
  /** Piksel per mm pada kertas (rata-rata). */
  pxPerMm: number;
  /** Ketajaman ternormalisasi (makin besar makin tajam). */
  sharpness: number;
  /** Sudut sudut-sudut segiempat (derajat). */
  minAngle: number;
  maxAngle: number;
  /** Daftar masalah yang ditemukan (bahasa Indonesia, siap tampil). */
  issues: string[];
  ok: boolean;
}

export interface SheetDetection {
  ok: boolean;
  reason?: string;
  corners?: Corners;
  H?: Homography;
  /** Skor gelap bar orientasi (0..1). */
  orientationScore?: number;
  /** Rotasi yang dipakai (0..3 x 90 derajat relatif urutan konveks). */
  rotation?: number;
  /** Luas segiempat marker / luas frame. */
  areaFrac?: number;
  candidates?: number;
  /** Skor kecocokan isi template 0..1. */
  fit?: number;
}
