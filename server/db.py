import datetime as dt
import uuid

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from server import config

_is_sqlite = config.DATABASE_URL.startswith("sqlite")
engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _id():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class ScanSession(Base):
    """Satu sesi pengawas: identitas + kumpulan berkas yang diunggah."""
    __tablename__ = "scan_session"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    nama_pengawas: Mapped[str] = mapped_column(String(200))
    hp: Mapped[str] = mapped_column(String(40))
    ruangan: Mapped[str] = mapped_column(String(100))
    kelas: Mapped[str] = mapped_column(String(100), default="")
    fakultas: Mapped[str] = mapped_column(String(100))
    prodi: Mapped[str] = mapped_column(String(150))
    # Diisi pengawas di form awal (sebelum unggah): kode paket soal yg dipakai kelas ini & hari ujian --
    # label bantu admin memantau, terpisah dari "Kode Soal" hasil baca LJK per-mahasiswa.
    kode_soal: Mapped[str] = mapped_column(String(100), default="")
    hari_ujian: Mapped[str] = mapped_column(String(20), default="")
    submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # sinkron ke Google Sheet (outbox): synced_at kosong = belum terkirim
    synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_attempts: Mapped[int] = mapped_column(Integer, default=0)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_next: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Validasi ADMIN (terpisah dari validasi per-lembar pengawas / Sheet.validated + ScanSession.submitted):
    # dicentang manual dari dashboard admin setelah admin mengecek sesi yg sudah selesai discan.
    admin_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    admin_validated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    admin_validated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)   # identitas admin (email/username) yg terakhir menandai validated
    files: Mapped[list["UploadFile"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    sheets: Mapped[list["Sheet"]] = relationship(back_populates="session", cascade="all, delete-orphan", order_by="Sheet.seq")


class UploadFile(Base):
    __tablename__ = "upload_file"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("scan_session.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default="queued")  # queued|processing|done|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    session: Mapped[ScanSession] = relationship(back_populates="files")


class Sheet(Base):
    """Satu lembar LJK hasil pindai (satu halaman)."""
    __tablename__ = "sheet"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("scan_session.id"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("upload_file.id"), index=True)
    page: Mapped[int] = mapped_column(Integer, default=0)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    doc_name: Mapped[str] = mapped_column(String(300))
    scan_status: Mapped[str] = mapped_column(String(120), default="")
    record: Mapped[dict] = mapped_column(JSON, default=dict)
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    session: Mapped[ScanSession] = relationship(back_populates="sheets")


class Kunci(Base):
    """Kunci jawaban per kode soal (disinkronkan dari Google Sheet oleh admin)."""
    __tablename__ = "kunci"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)  # {"1": "A", "2": "C", ...}
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | gsheet | upload
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TemplateCalib(Base):
    """Koreksi posisi kotak template per blok (mis. NAMA, NPM, KODE SOAL, Soal-A) -- offset (dx, dy) dlm
    piksel kanvas template (1700x2400) yg DITAMBAHKAN ke posisi asli setiap bubble blok itu saat memindai.
    Dipakai menu Kalibrasi di admin utk membetulkan pergeseran cetak/pindai kecil TANPA mengedit file
    template JSON -- lihat scanner.service.apply_field_calib (satu-satunya tempat nilai ini dipakai)."""
    __tablename__ = "template_calib"
    field_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    dx: Mapped[float] = mapped_column(default=0.0)
    dy: Mapped[float] = mapped_column(default=0.0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AdminUser(Base):
    """Akun admin password yg dikelola lewat menu admin sendiri (tab Akun) -- beda dgn ADMIN_USER/
    ADMIN_ACCOUNTS di env (server/config.py): akun di sini bisa ditambah/dinonaktifkan/reset password
    tanpa ubah deploy/.env & redeploy. auth._admin_accounts() menggabungkan KEDUANYA; lihat juga
    server/admin.py bag. "akun admin" utk endpoint CRUD-nya.

    Bisa juga ditambah langsung lewat psql (tanpa lewat UI), karena auth._check_password menerima DUA
    format password_hash: PBKDF2 "iters:salt_hex:hash_hex" (buatan auth.hash_password, dipakai tab Akun)
    ATAU hash bcrypt "$2..." (pgcrypto, extension-nya sudah diaktifkan otomatis di _migrate()). Contoh:
        INSERT INTO admin_user (id, username, password_hash, name, hp, active, created_at, created_by)
        VALUES (gen_random_uuid()::text, 'budi', crypt('passwordnya', gen_salt('bf')), 'Budi',
                '6281234567890', true, now(), 'psql');
    """
    __tablename__ = "admin_user"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200), default="")
    hp: Mapped[str] = mapped_column(String(40), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "admin" (default, akun biasa) atau "super_admin" (HANYA username config.SUPER_ADMIN_USERNAME = wsningrat:
    # boleh membuka menu Akun & menghapus sesi). Dijaga tiap startup oleh _sync_admin_types().
    type: Mapped[str] = mapped_column(String(20), default="admin", server_default="admin")


class Pengawas(Base):
    """Daftar pengawas yg muncul di dropdown "Nama Pengawas" halaman pengawas (/ljk) -- GANTI dari berkas
    pengawas.tsv (Docker secret /run/secrets/pengawas.tsv, perlu redeploy utk ubah -- lihat server/refdata.py
    versi lama) ke tabel DB biasa, supaya admin bisa tambah/ubah langsung lewat psql TANPA redeploy &
    langsung kebaca di FE (refdata.pengawas() query tabel ini tiap panggil, TANPA cache proses).
    nim KOSONG dianggap dosen HANYA bila namanya memuat gelar akademik (lihat refdata._looks_like_dosen) --
    nim kosong TANPA gelar dianggap mahasiswa (banyak kejadian di data nyata: migrasi lama & entri
    "Lainnya -- isi sendiri" sama2 nim="" walau org-nya mahasiswa, lihat refdata.pengawas()). hp kosong &
    BUKAN dosen -> pengawas WAJIB isi no HP sendiri saat pilih namanya di FE (lihat needs_hp di
    server/main.py /api/meta & showHp di server/static/app.js) -- jadi admin tak perlu isi hp di sini kalau belum tahu.

    Tambah lewat psql, mis.:
        INSERT INTO pengawas (id, nama, nim, hp) VALUES (gen_random_uuid()::text, 'Nama Pengawas', '', '');
    (nim diisi NIM mahasiswa, atau dikosongkan kalau dosen; hp diisi format +62xxxxxxxxxx atau dikosongkan)
    """
    __tablename__ = "pengawas"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    nama: Mapped[str] = mapped_column(String(200))
    nim: Mapped[str] = mapped_column(String(30), default="")
    hp: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AdminAccessLog(Base):
    """Riwayat tiap kali SESEORANG mencoba masuk admin -- lintas SEMUA jalur login (Google, Microsoft,
    username+password). Beda dgn AdminUser.last_login_at (cuma simpan yg PALING BARU, & cuma utk akun
    password di DB): ini riwayat LENGKAP, termasuk akun SSO & akun password dari env, utk audit "siapa
    akses kapan" (tab Akun -> Riwayat akses). Login via header X-Admin-Token TIDAK dicatat di sini --
    itu dipakai per-permintaan API, bukan peristiwa "masuk" satu kali, jadi akan membanjiri tabel ini."""
    __tablename__ = "admin_access_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    identity: Mapped[str] = mapped_column(String(200))
    method: Mapped[str] = mapped_column(String(20))   # google | microsoft | password
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)   # alasan gagal, mis. "ditolak"/"salah password"
    ip: Mapped[str] = mapped_column(String(64), default="")
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


def init_db():
    Base.metadata.create_all(engine)
    _migrate()
    _seed_env_admin_accounts()
    _sync_admin_types()
    _seed_pengawas_from_tsv()


def _migrate():
    """Migrasi ringan: tambah kolom baru pada tabel lama (create_all tidak mengubah tabel yang ada)."""
    from sqlalchemy import inspect, text
    wanted = {
        "scan_session": {
            "kelas": "VARCHAR(100) DEFAULT ''",
            "synced_at": "TIMESTAMP", "sync_attempts": "INTEGER DEFAULT 0",
            "sync_error": "TEXT", "sync_next": "TIMESTAMP",
            "admin_validated": "BOOLEAN DEFAULT FALSE", "admin_validated_at": "TIMESTAMP", "admin_validated_by": "VARCHAR(200)",
            "kode_soal": "VARCHAR(100) DEFAULT ''", "hari_ujian": "VARCHAR(20) DEFAULT ''",
        },
        "kunci": {"source": "VARCHAR(20) DEFAULT 'manual'", "updated_at": "TIMESTAMP"},
        "admin_user": {"hp": "VARCHAR(40) DEFAULT ''", "type": "VARCHAR(20) NOT NULL DEFAULT 'admin'"},
    }
    if not _is_sqlite:
        # pgcrypto: supaya admin_user.password_hash bisa diisi langsung lewat psql pakai crypt()/
        # gen_salt('bf') (lihat docstring AdminUser) -- tanpa ini fungsi itu tak tersedia di Postgres.
        # Transaksi & try/except TERPISAH dari migrasi kolom di bawah: kalau role DB-nya tak punya izin
        # bikin extension, transaksi ini gagal sendiri tanpa ikut membatalkan ALTER TABLE lainnya.
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        except Exception:  # noqa: BLE001
            pass
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in wanted.items():
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _sync_admin_types():
    """Jaga aturan tipe akun tiap startup: super_admin HANYA utk config.SUPER_ADMIN_USERNAME (wsningrat);
    akun lain apa pun nilai type-nya (mis. diubah manual lewat psql) dikembalikan ke "admin"."""
    from server import config
    try:
        with SessionLocal() as db:
            for u in db.query(AdminUser):
                want = "super_admin" if u.username == config.SUPER_ADMIN_USERNAME else "admin"
                if u.type != want:
                    u.type = want
            db.commit()
    except Exception:  # noqa: BLE001 -- tak boleh menggagalkan startup
        pass


def _seed_env_admin_accounts():
    """Migrasi SEKALI jalan: salin akun admin yg masih hardcode di env (ADMIN_USER/ADMIN_PASSWORD_HASH &
    ADMIN_ACCOUNTS di deploy/.env -- lihat server/config.py) ke tabel admin_user, supaya ke depannya
    dikelola lewat tab Akun/psql TANPA perlu redeploy. Hash disalin APA ADANYA (tak dihitung ulang --
    formatnya kompatibel, lihat auth._check_password), jadi login lewat akun itu tetap jalan sama persis.
    Idempoten & aman diulang tiap startup: cuma menambah username yg BELUM ada di tabel ini; sekali sudah
    ada (baik dari migrasi ini maupun dibuat manual), baris di env tak disentuh/ditimpa lagi -- boleh
    dihapus dari deploy/.env begitu sudah dicek bisa login lewat akun hasil migrasi ini."""
    from server import config
    pairs = []
    if config.ADMIN_USER and config.ADMIN_PASSWORD_HASH:
        pairs.append((config.ADMIN_USER, config.ADMIN_PASSWORD_HASH))
    for part in config.ADMIN_ACCOUNTS.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        user, h = part.split(":", 1)
        user, h = user.strip(), h.strip()
        if user and h:
            pairs.append((user, h))
    if not pairs:
        return
    try:
        with SessionLocal() as db:
            existing = {u for (u,) in db.query(AdminUser.username)}
            added = False
            for user, h in pairs:
                if user not in existing:
                    db.add(AdminUser(username=user, password_hash=h, name=user, active=True,
                                      created_by="migrasi otomatis dari .env"))
                    existing.add(user)
                    added = True
            if added:
                db.commit()
    except Exception:  # noqa: BLE001 -- jangan sampai startup server gagal total krn migrasi kenyamanan ini
        pass


def _seed_pengawas_from_tsv():
    """Migrasi SEKALI jalan: salin isi berkas pengawas.tsv lama (PENGAWAS_FILE / data/pengawas.tsv --
    lihat server/refdata.py versi sebelum tabel `pengawas` ada) ke tabel pengawas, supaya data yg sudah
    terdaftar tak hilang saat pindah dari berkas ke DB. id baris yg dimigrasi SENGAJA pakai skema id lama
    (hash nama+nim, bukan uuid acak spt baris baru) supaya idempoten: dipanggil ulang tiap startup tak
    pernah menggandakan baris yg sama. Berkas sumbernya boleh dihapus kapan saja setelah ini sukses
    sekali -- refdata.pengawas() sudah TAK PERNAH membaca berkas itu lagi, murni dari tabel ini."""
    import hashlib
    import os
    import re
    path = os.environ.get("PENGAWAS_FILE", "/run/secrets/pengawas.tsv")
    fallback = os.path.join(os.path.dirname(__file__), "..", "data", "pengawas.tsv")
    path = path if os.path.exists(path) else fallback
    if not os.path.exists(path):
        return

    def norm_hp(raw):
        d = re.sub(r"\D", "", raw or "")
        if d.startswith("62"):
            d = d[2:]
        d = d.lstrip("0")
        return "+62" + d if re.fullmatch(r"8\d{8,11}", d) else ""

    try:
        with open(path, encoding="utf-8-sig") as f:
            lines = [ln.rstrip("\r\n") for ln in f if ln.strip()]
        rows = [ln.split("\t") for ln in lines[1:]]
    except Exception:  # noqa: BLE001
        return
    try:
        with SessionLocal() as db:
            existing = {p for (p,) in db.query(Pengawas.id)}
            added = False
            for r in rows:
                r += [""] * (3 - len(r))
                nama, nim, hp = r[0].strip(), r[1].strip(), r[2].strip()
                if not nama:
                    continue
                pid = hashlib.sha1((nama + nim).encode()).hexdigest()[:10]
                if pid not in existing:
                    db.add(Pengawas(id=pid, nama=nama, nim=nim, hp=norm_hp(hp)))
                    existing.add(pid)
                    added = True
            if added:
                db.commit()
    except Exception:  # noqa: BLE001
        pass
