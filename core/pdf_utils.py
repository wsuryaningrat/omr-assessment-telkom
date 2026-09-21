try:
    import fitz  # PyMuPDF
    HAVE_PYMUPDF = True
except ImportError:
    HAVE_PYMUPDF = False

try:
    import pypdfium2 as pdfium
    HAVE_PDFIUM = True
except ImportError:
    HAVE_PDFIUM = False

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HAVE_HEIF = True
except ImportError:
    HAVE_HEIF = False

from PIL import Image, ImageOps
import cv2
import numpy as np
import io

def load_image_with_exif(file_bytes_or_buffer, ext=None, max_side=None):
    """
    Load an image from bytes/buffer and automatically correct orientation using EXIF tags.
    Prevents smartphone camera photos from being rotated/skewed (miring).

    HEIC/HEIF files are decoded directly via pillow_heif's own API rather than
    relying on PIL's opener auto-registration, which can silently fail to be
    picked up depending on import order/environment (seen as "cannot identify
    image file" even when pillow_heif is installed).
    """
    data = file_bytes_or_buffer if isinstance(file_bytes_or_buffer, bytes) else file_bytes_or_buffer.read()
    ext = (ext or "").lower().lstrip(".")

    pil_img = None
    if ext in ("heic", "heif"):
        if not HAVE_HEIF:
            raise ValueError("Dukungan berkas HEIC/HEIF tidak tersedia. Install dengan: pip install pillow-heif")
        heif_file = pillow_heif.open_heif(io.BytesIO(data), convert_hdr_to_8bit=True)
        pil_img = heif_file.to_pillow()
    else:
        try:
            pil_img = Image.open(io.BytesIO(data))
        except Exception:
            # Fallback: some phones export HEIC content under a .jpg/.jpeg extension
            if HAVE_HEIF:
                heif_file = pillow_heif.open_heif(io.BytesIO(data), convert_hdr_to_8bit=True)
                pil_img = heif_file.to_pillow()
            else:
                raise

    # Perkecil sejak decode (JPEG draft mode) supaya foto 12 MP tidak pernah
    # menempati RAM penuh (~36 MB) — cukup sisi terpanjang <= max_side.
    if max_side:
        try:
            pil_img.draft("RGB", (max_side, max_side))
        except Exception:
            pass

    # Correct smartphone EXIF orientation (critical for iPhone / Android scans)
    pil_img = ImageOps.exif_transpose(pil_img)
    pil_img = pil_img.convert("RGB")
    if max_side and max(pil_img.size) > max_side:
        scale = max_side / float(max(pil_img.size))
        pil_img = pil_img.resize(
            (max(1, round(pil_img.width * scale)), max(1, round(pil_img.height * scale))),
            Image.LANCZOS if hasattr(Image, "LANCZOS") else Image.BICUBIC,
        )
    bgr_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    return bgr_img


def iter_images_from_file(uploaded_file, target_dpi=200, max_side=None):
    """
    Generator: yields (page_name, bgr_image) satu halaman per iterasi supaya pemanggil
    bisa memproses lalu membuang gambar sebelum halaman berikutnya di-decode
    (RAM tetap ~1 gambar, bukan seluruh dokumen). Mendukung JPG/PNG/HEIC/WEBP dan PDF
    multi-halaman (PyMuPDF, fallback pypdfium2). `max_side` membatasi sisi terpanjang.
    """
    file_bytes = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    filename = uploaded_file.name if hasattr(uploaded_file, "name") else "document"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    def _limit(bgr):
        if max_side and max(bgr.shape[:2]) > max_side:
            f = max_side / float(max(bgr.shape[:2]))
            bgr = cv2.resize(bgr, (max(1, round(bgr.shape[1] * f)), max(1, round(bgr.shape[0] * f))), interpolation=cv2.INTER_AREA)
        return bgr

    if ext != "pdf":
        yield filename, load_image_with_exif(file_bytes, ext=ext, max_side=max_side)
        return

    yielded = 0
    if HAVE_PYMUPDF:
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            try:
                mat = fitz.Matrix(target_dpi / 72.0, target_dpi / 72.0)
                num_pages = len(doc)
                for page_idx in range(num_pages):
                    pix = doc.load_page(page_idx).get_pixmap(matrix=mat, alpha=False)
                    img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, 3))
                    bgr_img = _limit(cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR))
                    del pix, img_np
                    yielded += 1
                    yield (f"{filename} (Hal {page_idx + 1}/{num_pages})" if num_pages > 1 else filename), bgr_img
            finally:
                doc.close()
            return
        except Exception:
            if yielded:
                raise

    if HAVE_PDFIUM:
        try:
            pdf = pdfium.PdfDocument(file_bytes)
            num_pages = len(pdf)
            scale = target_dpi / 72.0
            for page_idx in range(num_pages):
                page = pdf[page_idx]
                pil_img = page.render(scale=scale).to_pil().convert("RGB")
                bgr_img = _limit(cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR))
                page.close()
                yield (f"{filename} (Hal {page_idx + 1}/{num_pages})" if num_pages > 1 else filename), bgr_img
            pdf.close()
            return
        except Exception as e:
            raise ValueError(f"Gagal membaca PDF {filename}: {str(e)}")

    raise ValueError("Library pembaca PDF (PyMuPDF / pypdfium2) belum terpasang.")


def extract_images_from_file(uploaded_file, target_dpi=200):
    """Versi list dari iter_images_from_file (dipakai mode kalibrasi/reader)."""
    return list(iter_images_from_file(uploaded_file, target_dpi=target_dpi))
