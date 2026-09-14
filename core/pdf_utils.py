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

def load_image_with_exif(file_bytes_or_buffer, ext=None):
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

    # Correct smartphone EXIF orientation (critical for iPhone / Android scans)
    pil_img = ImageOps.exif_transpose(pil_img)
    pil_img = pil_img.convert("RGB")
    bgr_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    return bgr_img


def extract_images_from_file(uploaded_file, target_dpi=200):
    """
    Extracts one or more OpenCV BGR images from an uploaded file (JPG, PNG, or multi-page PDF).
    Uses high-performance, crash-proof PyMuPDF (fitz) on macOS with fallback to pypdfium2.
    Returns a list of tuples: [(page_name, bgr_image), ...]
    """
    file_bytes = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    filename = uploaded_file.name if hasattr(uploaded_file, "name") else "document"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "pdf":
        images = []
        if HAVE_PYMUPDF:
            try:
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                zoom = target_dpi / 72.0
                mat = fitz.Matrix(zoom, zoom)
                num_pages = len(doc)
                for page_idx in range(num_pages):
                    page = doc.load_page(page_idx)
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                    img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, 3))
                    bgr_img = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
                    page_name = f"{filename} (Hal {page_idx + 1}/{num_pages})" if num_pages > 1 else filename
                    images.append((page_name, bgr_img))
                doc.close()
                return images
            except Exception as e:
                images = []

        if HAVE_PDFIUM:
            try:
                pdf = pdfium.PdfDocument(file_bytes)
                num_pages = len(pdf)
                scale = target_dpi / 72.0
                for page_idx in range(num_pages):
                    page = pdf[page_idx]
                    pil_img = page.render(scale=scale).to_pil()
                    pil_img = pil_img.convert("RGB")
                    bgr_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
                    page_name = f"{filename} (Hal {page_idx + 1}/{num_pages})" if num_pages > 1 else filename
                    images.append((page_name, bgr_img))
                    page.close()
                pdf.close()
                return images
            except Exception as e:
                raise ValueError(f"Gagal membaca PDF {filename}: {str(e)}")

        raise ValueError("Library pembaca PDF (PyMuPDF / pypdfium2) belum terpasang.")

    else:
        # Standard image (JPG, PNG, HEIC, HEIF, WEBP, ...) with EXIF auto-correction
        bgr_img = load_image_with_exif(file_bytes, ext=ext)
        return [(filename, bgr_img)]
