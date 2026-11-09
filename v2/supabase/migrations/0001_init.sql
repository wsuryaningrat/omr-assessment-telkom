-- =============================================================================
-- OMR LJK v2 — skema Supabase
-- Jalankan di Supabase SQL Editor (atau `supabase db push`).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Pengawas (whitelist login)
-- -----------------------------------------------------------------------------
create table if not exists public.pengawas (
  email       text primary key check (email = lower(email)),
  nama        text not null,
  role        text not null default 'pengawas' check (role in ('pengawas', 'admin')),
  no_hp       text,
  keterangan  text,
  aktif       boolean not null default true,
  created_at  timestamptz not null default now()
);

create or replace function public.current_email()
returns text language sql stable as $$
  select lower(coalesce(auth.jwt() ->> 'email', ''))
$$;

create or replace function public.is_pengawas()
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.pengawas where email = public.current_email() and aktif)
$$;

create or replace function public.is_admin()
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.pengawas where email = public.current_email() and aktif and role = 'admin')
$$;

-- -----------------------------------------------------------------------------
-- Ujian (gelombang), kelas, peserta
-- -----------------------------------------------------------------------------
create table if not exists public.ujian (
  id                text primary key,              -- mis. 'pendikar-2026'
  nama              text not null,
  template_version  text not null default 'ljk-v2-pendikar-1',
  aktif             boolean not null default true,
  created_at        timestamptz not null default now()
);

create table if not exists public.kelas (
  id               text primary key,               -- kode kelas, mis. 'BS1DS-50-REG-01'
  ujian_id         text not null references public.ujian(id) on update cascade,
  prodi            text,
  kode_mk          text,
  dosen            text,
  ruangan          text,
  hari             text,
  waktu            text,
  jumlah_mahasiswa int,
  created_at       timestamptz not null default now()
);
create index if not exists kelas_ujian_idx on public.kelas(ujian_id);

create table if not exists public.peserta (
  kelas_id    text not null references public.kelas(id) on update cascade on delete cascade,
  nim         text not null,
  nama        text not null,
  kelas_asal  text,
  status      text,
  primary key (kelas_id, nim)
);
create index if not exists peserta_nim_idx on public.peserta(nim);

-- Kunci jawaban: hanya admin yang bisa melihat (pengawas = relawan, jangan bocor).
create table if not exists public.kunci (
  ujian_id    text not null references public.ujian(id) on update cascade on delete cascade,
  kode_soal   text not null,
  jawaban     jsonb not null,                      -- {"1":["B"],"2":["A","C"],...}
  updated_by  text default public.current_email(),
  updated_at  timestamptz not null default now(),
  primary key (ujian_id, kode_soal)
);

-- -----------------------------------------------------------------------------
-- Submissions (1 foto LJK = 1 baris)
-- -----------------------------------------------------------------------------
do $$ begin
  create type public.submission_status as enum ('perlu_validasi', 'valid', 'ditolak', 'diganti');
exception when duplicate_object then null; end $$;

create table if not exists public.submissions (
  id            uuid primary key default gen_random_uuid(),
  ujian_id      text not null references public.ujian(id) on update cascade,
  kelas_id      text not null references public.kelas(id) on update cascade,
  nim           text not null,                     -- NIM final (dikonfirmasi pengawas)
  nim_terbaca   text,                              -- hasil OMR mentah (boleh ada '?')
  nim_sumber    text not null default 'omr' check (nim_sumber in ('omr', 'dropdown', 'manual', 'validator')),
  kode_soal     text,
  jawaban       jsonb not null,                    -- array 75: "A".."D" | null
  kuisioner     jsonb not null,                    -- array 15
  omr           jsonb not null,                    -- skor per bubble, status, pojok, kualitas, versi template
  flags         int not null default 0,            -- jumlah item ragu/ganda
  photo_path    text not null,
  uploaded_by   text not null default public.current_email(),
  uploaded_at   timestamptz not null default now(),
  status        public.submission_status not null default 'perlu_validasi',
  validated_by  text,
  validated_at  timestamptz,
  catatan       text,
  updated_at    timestamptz not null default now()
);
create index if not exists submissions_kelas_idx on public.submissions(kelas_id, status);
create unique index if not exists submissions_one_active_per_nim
  on public.submissions(ujian_id, nim) where status in ('perlu_validasi', 'valid');

-- -----------------------------------------------------------------------------
-- Audit log (otomatis via trigger)
-- -----------------------------------------------------------------------------
create table if not exists public.audit_log (
  id             bigserial primary key,
  at             timestamptz not null default now(),
  actor          text not null default public.current_email(),
  submission_id  uuid references public.submissions(id) on delete set null,
  action         text not null,
  detail         jsonb
);
create index if not exists audit_submission_idx on public.audit_log(submission_id);

create or replace function public.submissions_audit()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  changes jsonb := '{}'::jsonb;
begin
  -- pengawas tidak boleh memalsukan identitas validator / uploader
  new.uploaded_by := old.uploaded_by;
  new.uploaded_at := old.uploaded_at;
  new.photo_path := old.photo_path;
  new.omr := old.omr;
  new.updated_at := now();
  if new.status is distinct from old.status then
    if new.status in ('valid', 'ditolak') then
      new.validated_by := public.current_email();
      new.validated_at := now();
    elsif new.status = 'perlu_validasi' then
      new.validated_by := null;
      new.validated_at := null;
    end if;
  else
    new.validated_by := old.validated_by;
    new.validated_at := old.validated_at;
  end if;

  if new.nim is distinct from old.nim then changes := changes || jsonb_build_object('nim', jsonb_build_array(old.nim, new.nim)); end if;
  if new.kode_soal is distinct from old.kode_soal then changes := changes || jsonb_build_object('kode_soal', jsonb_build_array(old.kode_soal, new.kode_soal)); end if;
  if new.status is distinct from old.status then changes := changes || jsonb_build_object('status', jsonb_build_array(old.status, new.status)); end if;
  if new.catatan is distinct from old.catatan then changes := changes || jsonb_build_object('catatan', new.catatan); end if;
  if new.jawaban is distinct from old.jawaban then
    changes := changes || jsonb_build_object('jawaban', (
      select coalesce(jsonb_object_agg((i)::text, jsonb_build_array(old.jawaban -> (i - 1), new.jawaban -> (i - 1))), '{}'::jsonb)
      from generate_series(1, greatest(jsonb_array_length(old.jawaban), jsonb_array_length(new.jawaban))) i
      where (old.jawaban -> (i - 1)) is distinct from (new.jawaban -> (i - 1))));
  end if;
  if new.kuisioner is distinct from old.kuisioner then
    changes := changes || jsonb_build_object('kuisioner', (
      select coalesce(jsonb_object_agg((i)::text, jsonb_build_array(old.kuisioner -> (i - 1), new.kuisioner -> (i - 1))), '{}'::jsonb)
      from generate_series(1, greatest(jsonb_array_length(old.kuisioner), jsonb_array_length(new.kuisioner))) i
      where (old.kuisioner -> (i - 1)) is distinct from (new.kuisioner -> (i - 1))));
  end if;
  if changes <> '{}'::jsonb then
    insert into public.audit_log(submission_id, action, detail) values (new.id, 'edit', changes);
  end if;
  return new;
end $$;

create or replace function public.submissions_audit_insert()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.audit_log(submission_id, action, detail)
  values (new.id, 'upload', jsonb_build_object('kelas', new.kelas_id, 'nim', new.nim, 'nim_terbaca', new.nim_terbaca, 'nim_sumber', new.nim_sumber, 'flags', new.flags));
  return new;
end $$;

drop trigger if exists submissions_audit_trg on public.submissions;
create trigger submissions_audit_trg
  before update on public.submissions
  for each row execute function public.submissions_audit();

drop trigger if exists submissions_audit_ins_trg on public.submissions;
create trigger submissions_audit_ins_trg
  after insert on public.submissions
  for each row execute function public.submissions_audit_insert();

-- -----------------------------------------------------------------------------
-- RPC: simpan LJK (opsional menggantikan submission lama untuk NIM yang sama)
-- -----------------------------------------------------------------------------
create or replace function public.simpan_ljk(payload jsonb, ganti boolean default false)
returns uuid language plpgsql security invoker set search_path = public as $$
declare
  new_id uuid;
begin
  if not public.is_pengawas() then
    raise exception 'Akun belum terdaftar sebagai pengawas';
  end if;
  if ganti then
    update public.submissions
       set status = 'diganti',
           catatan = coalesce(catatan || ' | ', '') || 'diganti oleh upload baru ' || to_char(now() at time zone 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI')
     where ujian_id = payload ->> 'ujian_id'
       and nim = payload ->> 'nim'
       and status in ('perlu_validasi', 'valid');
  end if;
  insert into public.submissions (ujian_id, kelas_id, nim, nim_terbaca, nim_sumber, kode_soal, jawaban, kuisioner, omr, flags, photo_path)
  values (
    payload ->> 'ujian_id',
    payload ->> 'kelas_id',
    payload ->> 'nim',
    payload ->> 'nim_terbaca',
    coalesce(payload ->> 'nim_sumber', 'omr'),
    payload ->> 'kode_soal',
    payload -> 'jawaban',
    payload -> 'kuisioner',
    payload -> 'omr',
    coalesce((payload ->> 'flags')::int, 0),
    payload ->> 'photo_path'
  )
  returning id into new_id;
  return new_id;
end $$;

-- -----------------------------------------------------------------------------
-- Penilaian (hanya admin) — kunci tidak pernah dikirim ke pengawas
-- -----------------------------------------------------------------------------
create or replace function public.nilai_jawaban(jawaban jsonb, kunci jsonb)
returns table (benar int, salah int, kosong int, total int, nilai numeric)
language sql immutable as $$
  select
    count(*) filter (where j.v is not null and k.value ? j.v)::int,
    count(*) filter (where j.v is not null and not (k.value ? j.v))::int,
    count(*) filter (where j.v is null)::int,
    count(*)::int,
    round(100.0 * count(*) filter (where j.v is not null and k.value ? j.v) / nullif(count(*), 0), 2)
  from jsonb_each(kunci) k
  left join lateral (select nullif(jawaban ->> ((k.key)::int - 1), '') as v) j on true
$$;

create or replace function public.rekap_nilai(p_ujian text)
returns table (
  submission_id uuid, kelas_id text, prodi text, nim text, nama text, kode_soal text,
  benar int, salah int, kosong int, total int, nilai numeric,
  status public.submission_status, flags int, uploaded_by text, uploaded_at timestamptz,
  validated_by text, validated_at timestamptz, jawaban jsonb, kuisioner jsonb
)
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.is_admin() then
    raise exception 'Hanya admin yang dapat melihat nilai';
  end if;
  return query
  select s.id, s.kelas_id, k.prodi, s.nim, p.nama, s.kode_soal,
         n.benar, n.salah, n.kosong, n.total, n.nilai,
         s.status, s.flags, s.uploaded_by, s.uploaded_at, s.validated_by, s.validated_at, s.jawaban, s.kuisioner
  from public.submissions s
  join public.kelas k on k.id = s.kelas_id
  left join public.peserta p on p.kelas_id = s.kelas_id and p.nim = s.nim
  left join public.kunci kc on kc.ujian_id = s.ujian_id and kc.kode_soal = s.kode_soal
  left join lateral public.nilai_jawaban(s.jawaban, kc.jawaban) n on kc.jawaban is not null
  where s.ujian_id = p_ujian and s.status in ('perlu_validasi', 'valid')
  order by s.kelas_id, s.nim;
end $$;

-- Progres per kelas (dipakai dashboard, semua pengawas boleh)
create or replace view public.progres_kelas with (security_invoker = true) as
select
  k.id as kelas_id, k.ujian_id, k.prodi, k.ruangan, k.hari, k.waktu,
  (select count(*) from public.peserta p where p.kelas_id = k.id)::int as total_peserta,
  count(s.id) filter (where s.status in ('perlu_validasi', 'valid'))::int as terupload,
  count(s.id) filter (where s.status = 'valid')::int as tervalidasi,
  count(s.id) filter (where s.status = 'perlu_validasi')::int as perlu_validasi,
  count(s.id) filter (where s.status = 'perlu_validasi' and s.flags > 0)::int as perlu_cek
from public.kelas k
left join public.submissions s on s.kelas_id = k.id
group by k.id;

-- -----------------------------------------------------------------------------
-- Row Level Security
-- -----------------------------------------------------------------------------
alter table public.pengawas    enable row level security;
alter table public.ujian       enable row level security;
alter table public.kelas       enable row level security;
alter table public.peserta     enable row level security;
alter table public.kunci       enable row level security;
alter table public.submissions enable row level security;
alter table public.audit_log   enable row level security;

-- pengawas
drop policy if exists pengawas_read on public.pengawas;
create policy pengawas_read on public.pengawas for select to authenticated
  using (public.is_pengawas() or email = public.current_email());
drop policy if exists pengawas_admin_write on public.pengawas;
create policy pengawas_admin_write on public.pengawas for all to authenticated
  using (public.is_admin()) with check (public.is_admin());

-- data referensi: baca = pengawas, tulis = admin
drop policy if exists ujian_read on public.ujian;
create policy ujian_read on public.ujian for select to authenticated using (public.is_pengawas());
drop policy if exists ujian_write on public.ujian;
create policy ujian_write on public.ujian for all to authenticated using (public.is_admin()) with check (public.is_admin());

drop policy if exists kelas_read on public.kelas;
create policy kelas_read on public.kelas for select to authenticated using (public.is_pengawas());
drop policy if exists kelas_write on public.kelas;
create policy kelas_write on public.kelas for all to authenticated using (public.is_admin()) with check (public.is_admin());

drop policy if exists peserta_read on public.peserta;
create policy peserta_read on public.peserta for select to authenticated using (public.is_pengawas());
drop policy if exists peserta_write on public.peserta;
create policy peserta_write on public.peserta for all to authenticated using (public.is_admin()) with check (public.is_admin());

drop policy if exists kunci_admin on public.kunci;
create policy kunci_admin on public.kunci for all to authenticated using (public.is_admin()) with check (public.is_admin());

-- submissions: semua pengawas boleh lihat & validasi kelas mana pun
drop policy if exists sub_read on public.submissions;
create policy sub_read on public.submissions for select to authenticated using (public.is_pengawas());
drop policy if exists sub_insert on public.submissions;
create policy sub_insert on public.submissions for insert to authenticated
  with check (public.is_pengawas() and uploaded_by = public.current_email() and status = 'perlu_validasi');
drop policy if exists sub_update on public.submissions;
create policy sub_update on public.submissions for update to authenticated
  using (public.is_pengawas()) with check (public.is_pengawas());
drop policy if exists sub_delete on public.submissions;
create policy sub_delete on public.submissions for delete to authenticated using (public.is_admin());

drop policy if exists audit_read on public.audit_log;
create policy audit_read on public.audit_log for select to authenticated using (public.is_pengawas());

-- Kolom yang boleh diubah pengawas (sisanya dikunci trigger + grant)
revoke update on public.submissions from authenticated;
grant update (nim, nim_sumber, kode_soal, jawaban, kuisioner, status, catatan, flags) on public.submissions to authenticated;
grant select on public.progres_kelas to authenticated;
revoke execute on function public.rekap_nilai(text) from anon;

-- -----------------------------------------------------------------------------
-- Storage: bucket privat untuk foto LJK
-- -----------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('ljk', 'ljk', false)
on conflict (id) do nothing;

drop policy if exists ljk_read on storage.objects;
create policy ljk_read on storage.objects for select to authenticated
  using (bucket_id = 'ljk' and public.is_pengawas());
drop policy if exists ljk_insert on storage.objects;
create policy ljk_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'ljk' and public.is_pengawas());
drop policy if exists ljk_delete on storage.objects;
create policy ljk_delete on storage.objects for delete to authenticated
  using (bucket_id = 'ljk' and public.is_admin());
