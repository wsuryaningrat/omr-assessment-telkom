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
except ImportError:
    pass

from PIL import Image, ImageOps
import cv2
import numpy as np
import io

def load_image_with_exif(file_bytes_or_buffer):
    """
    Load an image from bytes/buffer and automatically correct orientation using EXIF tags.
    Prevents smartphone camera photos from being rotated/skewed (miring).
    """
    if isinstance(file_bytes_or_buffer, bytes):
        pil_img = Image.open(io.BytesIO(file_bytes_or_buffer))
    else:
        pil_img = Image.open(file_bytes_or_buffer)

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
        # Standard image (JPG, PNG) with EXIF auto-correction
        bgr_img = load_image_with_exif(file_bytes)
        return [(filename, bgr_img)]
