-- 0003: log kirim ke Google Sheet per kelas + status terkirim di progres_kelas; kelas PJJ tidak dipakai.

create table if not exists public.kirim_log (
  id bigserial primary key,
  kelas_id text not null references public.kelas(id) on delete cascade,
  jumlah int not null,
  dikirim_by text not null default public.current_email(),
  dikirim_at timestamptz not null default now()
);
alter table public.kirim_log enable row level security;
drop policy if exists kirim_read on public.kirim_log;
drop policy if exists kirim_insert on public.kirim_log;
create policy kirim_read on public.kirim_log for select to authenticated using (public.is_pengawas());
create policy kirim_insert on public.kirim_log for insert to authenticated with check (public.is_admin());
grant select, insert on public.kirim_log to authenticated;
grant usage on sequence public.kirim_log_id_seq to authenticated;

create or replace view public.progres_kelas with (security_invoker = true) as
select
  k.id as kelas_id, k.ujian_id, k.prodi, k.ruangan, k.hari, k.waktu,
  (select count(*) from public.peserta p where p.kelas_id = k.id)::int as total_peserta,
  count(s.id) filter (where s.status in ('perlu_validasi', 'valid'))::int as terupload,
  count(s.id) filter (where s.status = 'valid')::int as tervalidasi,
  count(s.id) filter (where s.status = 'perlu_validasi')::int as perlu_validasi,
  count(s.id) filter (where s.status = 'perlu_validasi' and s.flags > 0)::int as perlu_cek,
  (select max(l.dikirim_at) from public.kirim_log l where l.kelas_id = k.id) as terkirim_at,
  (select l.jumlah from public.kirim_log l where l.kelas_id = k.id order by l.dikirim_at desc limit 1) as terkirim_jumlah
from public.kelas k
left join public.submissions s on s.kelas_id = k.id
group by k.id;
grant select on public.progres_kelas to authenticated;

-- Kelas PJJ (online) tidak ikut profiling: hapus beserta pesertanya.
delete from public.submissions where kelas_id ilike '%-PJJ-%';
delete from public.peserta where kelas_id ilike '%-PJJ-%';
delete from public.kelas where id ilike '%-PJJ-%';
