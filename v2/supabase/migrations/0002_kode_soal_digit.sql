-- =============================================================================
-- LJK v2-2: kode soal diarsir sebagai 3 digit (seperti NIM), bukan pilihan tetap.
-- Daftar kode yang berlaku disimpan per ujian (tidak rahasia, boleh dilihat pengawas).
-- Aman dijalankan berulang.
-- =============================================================================
alter table public.ujian add column if not exists kode_soal text[] not null default '{}';
alter table public.ujian alter column template_version set default 'ljk-v2-2';

-- isi otomatis dari kunci yang sudah ada
update public.ujian u
   set kode_soal = sub.kodes
  from (select ujian_id, array_agg(kode_soal order by kode_soal) as kodes from public.kunci group by ujian_id) sub
 where sub.ujian_id = u.id and coalesce(array_length(u.kode_soal, 1), 0) = 0;
