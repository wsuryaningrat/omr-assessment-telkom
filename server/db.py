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
    submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # sinkron ke Google Sheet (outbox): synced_at kosong = belum terkirim
    synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_attempts: Mapped[int] = mapped_column(Integer, default=0)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_next: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


def init_db():
    Base.metadata.create_all(engine)
    _migrate()


def _migrate():
    """Migrasi ringan: tambah kolom baru pada tabel lama (create_all tidak mengubah tabel yang ada)."""
    from sqlalchemy import inspect, text
    wanted = {
        "scan_session": {
            "kelas": "VARCHAR(100) DEFAULT ''",
            "synced_at": "TIMESTAMP", "sync_attempts": "INTEGER DEFAULT 0",
            "sync_error": "TEXT", "sync_next": "TIMESTAMP",
        },
        "kunci": {"source": "VARCHAR(20) DEFAULT 'manual'", "updated_at": "TIMESTAMP"},
    }
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in wanted.items():
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
