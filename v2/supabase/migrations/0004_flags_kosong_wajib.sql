-- 0004: jawaban KOSONG pada nomor wajib (kuisioner 1-8, soal 1-50) dihitung "ragu".
-- Aplikasi sudah menerapkan aturan ini untuk scan baru dan saat menampilkan LJK lama.
-- Skrip ini menghitung ulang kolom `flags` LJK yang masih "perlu validasi" supaya hitungan
-- "ditandai" di daftar kelas dan urutan validasi ikut aturan baru.
-- Aman dijalankan berulang: flags dihitung ulang dari nol, bukan ditambah.
-- (Kolom omr tidak diubah; trigger audit memang mengunci kolom itu. Perubahan flags tidak masuk audit_log.)

with hitung as (
  select
    s.id,
    (
      select count(*) from jsonb_array_elements(s.omr -> 'kuisioner') with ordinality as e(v, i)
      where e.v ->> 'status' in ('ragu', 'multi') or (e.i <= 8 and e.v ->> 'status' = 'blank')
    )
    + (
      select count(*) from jsonb_array_elements(s.omr -> 'soal') with ordinality as e(v, i)
      where e.v ->> 'status' in ('ragu', 'multi') or (e.i <= 50 and e.v ->> 'status' = 'blank')
    )
    -- sama dengan hitungan saat upload: NIM bukan hasil baca OMR, kode soal tidak terbaca/tidak berlaku,
    -- NIM di luar daftar peserta kelas
    + (s.nim_sumber <> 'omr')::int
    + (not coalesce(s.kode_soal = s.omr -> 'kode_soal' ->> 'value' and s.kode_soal = any (u.kode_soal), false))::int
    + (not exists (select 1 from public.peserta p where p.kelas_id = s.kelas_id and p.nim = s.nim))::int
    as flags
  from public.submissions s
  join public.ujian u on u.id = s.ujian_id
  where s.status = 'perlu_validasi'
)
update public.submissions s
   set flags = h.flags
  from hitung h
 where h.id = s.id
   and s.flags is distinct from h.flags;
