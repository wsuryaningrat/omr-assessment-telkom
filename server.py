import io
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import base64
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import concurrent.futures

import cv2
import numpy as np
import pandas as pd

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from core.alignment import detect_corners_and_crop
from core.decoder import decode_field_detailed
from core.evaluator import parse_kunci_jawaban_excel, grade_student_record, generate_sample_kunci_excel
from core.pdf_utils import extract_images_from_file
from core.enhancer import enhance_camscanner

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "template-final.json")
if not os.path.exists(TEMPLATE_PATH):
    TEMPLATE_PATH = os.path.join(BASE_DIR, "templates", "template_v1.json")

with open(TEMPLATE_PATH, "r") as f:
    CANONICAL_TEMPLATE = json.load(f)

app = FastAPI(
    title="Telkom University OMR Assessment API",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url=None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def process_single_bgr(img_bgr: np.ndarray, page_name: str) -> Dict[str, Any]:
    try:
        warped, pts, method, c_ids, d_name, status, aruco_reg = detect_corners_and_crop(
            img_bgr, preferred_method="aruco", crop_mode="inner", apply_standardization=True
        )
    except Exception as e:
        return {
            "id": f"{page_name}_{int(datetime.now().timestamp())}",
            "filename": page_name,
            "status": "FAILED",
            "error": f"Gagal deteksi: {str(e)}",
            "nama": "-",
            "npm": "-",
            "fakultas": "-",
            "kode_soal": "-",
            "answers": {},
            "survey": {},
            "ai_analysis": {},
            "total_answered": 0,
            "warped_image_b64": None,
            "enhanced_image_b64": None,
            "diagnostics": {"method": "none", "status": f"ERROR: {str(e)}"}
        }

    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    fields: Dict[str, str] = {}
    survey: Dict[str, str] = {}
    answers: Dict[str, str] = {}
    all_ai_analysis: Dict[str, Any] = {}

    for fname, fdef in CANONICAL_TEMPLATE.get("fields", {}).items():
        fcopy = dict(fdef)
        fcopy["field_name"] = fname
        decoded, analysis = decode_field_detailed(gray, fcopy, thresh=0.28, margin=0.08)
        for k, v in decoded.items():
            fields[k] = v
            if k.startswith("soal_") or k.startswith("Soal-") or k.startswith("Q"):
                answers[k] = v
            elif k.startswith("Kuisioner") or k.startswith("survey"):
                survey[k] = v
        all_ai_analysis.update(analysis)

    nama = fields.get("NAMA", "").strip() or "-"
    npm = fields.get("NPM", "").strip() or "-"
    fakultas = fields.get("FAKULTAS", "").strip() or "-"
    kode_soal = fields.get("KODE SOAL", fields.get("KODE_SOAL", "")).strip() or "-"

    filled_q = sum(1 for v in answers.values() if v not in ["BLANK", "?", "-", "", "None"])

    # High-clarity review preview (Height 1200px, quality 85)
    preview_h = 1200
    preview_w = int(1700 * (preview_h / 2400))
    resized_preview = cv2.resize(warped, (preview_w, preview_h), interpolation=cv2.INTER_AREA)
    _, buf_orig = cv2.imencode(".jpg", resized_preview, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    b64_orig = base64.b64encode(buf_orig).decode("utf-8")

    # CamScanner Magic Color & Clean B&W modes
    try:
        cam_preview = enhance_camscanner(resized_preview, mode="magic_color")
        _, buf_enh = cv2.imencode(".jpg", cam_preview, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        b64_enh = base64.b64encode(buf_enh).decode("utf-8")
    except Exception:
        b64_enh = b64_orig

    try:
        bw_preview = enhance_camscanner(resized_preview, mode="clean_bw")
        _, buf_bw = cv2.imencode(".jpg", bw_preview, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        b64_bw = base64.b64encode(buf_bw).decode("utf-8")
    except Exception:
        b64_bw = b64_orig

    sub_status = "OK" if status.startswith("DETECTED") and filled_q > 0 else "NEEDS_REVIEW"

    return {
        "id": f"{page_name}_{int(datetime.now().timestamp())}",
        "filename": page_name,
        "status": sub_status,
        "nama": nama,
        "npm": npm,
        "fakultas": fakultas,
        "kode_soal": kode_soal,
        "answers": answers,
        "survey": survey,
        "ai_analysis": all_ai_analysis,
        "total_answered": filled_q,
        "warped_image_b64": b64_orig,
        "enhanced_image_b64": b64_enh,
        "bw_image_b64": b64_bw,
        "diagnostics": {
            "method": method,
            "detected_markers": c_ids if c_ids is not None else [],
            "status_text": status
        }
    }


@app.get("/api/health")
@app.head("/api/health")
def health_check():
    return {"status": "ok", "app": "OMR Assessment Telkom", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/template")
@app.head("/api/template")
def get_template():
    return CANONICAL_TEMPLATE


@app.post("/api/scan")
async def scan_ljk_files(files: List[UploadFile] = File(...)):
    # Extract images from all uploaded files
    all_tasks = []

    for upload in files:
        contents = await upload.read()
        
        class ByteHolder:
            def __init__(self, data, name):
                self.data = data
                self.name = name
            def getvalue(self):
                return self.data

        holder = ByteHolder(contents, upload.filename)
        try:
            image_tuples = extract_images_from_file(holder, target_dpi=200)
            for page_name, bgr_img in image_tuples:
                all_tasks.append((page_name, bgr_img))
        except Exception as e:
            all_tasks.append((upload.filename, None))

    # Parallel processing using ThreadPoolExecutor for high throughput (9000 files scale)
    results = []
    futures = []

    for page_name, bgr_img in all_tasks:
        if bgr_img is None:
            results.append({
                "id": f"{page_name}_err",
                "filename": page_name,
                "status": "FAILED",
                "error": "Gagal mengekstrak berkas.",
                "nama": "-",
                "npm": "-",
                "fakultas": "-",
                "kode_soal": "-",
                "answers": {},
                "survey": {},
                "ai_analysis": {},
                "total_answered": 0,
                "warped_image_b64": None,
                "enhanced_image_b64": None
            })
        else:
            futures.append(EXECUTOR.submit(process_single_bgr, bgr_img, page_name))

    for fut in futures:
        try:
            res = fut.result()
            results.append(res)
        except Exception as e:
            results.append({
                "id": f"err_{int(datetime.now().timestamp())}",
                "filename": "Error",
                "status": "FAILED",
                "error": str(e),
                "nama": "-",
                "npm": "-",
                "fakultas": "-",
                "kode_soal": "-",
                "answers": {},
                "survey": {},
                "ai_analysis": {},
                "total_answered": 0,
                "warped_image_b64": None,
                "enhanced_image_b64": None
            })

    return JSONResponse(content={"total_scanned": len(results), "submissions": results})


@app.post("/api/grade")
async def grade_submissions(request: Request):
    submissions = []
    kunci_sheets = {}

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        subs_raw = form.get("submissions_json")
        if subs_raw:
            submissions = json.loads(subs_raw)
        key_file = form.get("answer_key_file")
        if key_file and hasattr(key_file, "read"):
            key_bytes = await key_file.read()
            if key_bytes:
                kunci_sheets = parse_kunci_jawaban_excel(io.BytesIO(key_bytes))
    else:
        try:
            body = await request.json()
            submissions = body.get("submissions", body.get("results", []))
            if "answer_keys" in body:
                kunci_sheets = body["answer_keys"]
        except Exception:
            pass

    if not kunci_sheets:
        sample_buf = generate_sample_kunci_excel()
        kunci_sheets = parse_kunci_jawaban_excel(sample_buf)

    graded_list = []
    scores = []
    fakultas_counts = {}

    for sub in submissions:
        student_record = {
            "Nama": sub.get("nama", "-"),
            "NPM": sub.get("npm", "-"),
            "Fakultas": sub.get("fakultas", "-"),
            "Kode Soal": sub.get("kode_soal", "-"),
            "Status Scan": sub.get("status", "OK"),
            "Filename": sub.get("filename", "-")
        }
        for k, v in sub.get("answers", {}).items():
            student_record[k] = v

        graded_rec = grade_student_record(student_record, kunci_sheets)
        
        sub_graded = dict(sub)
        sub_graded["nilai"] = graded_rec.get("Nilai", "-")
        sub_graded["benar"] = graded_rec.get("Jumlah Benar", "-")
        sub_graded["salah"] = graded_rec.get("Jumlah Salah", "-")
        sub_graded["kosong"] = graded_rec.get("Jumlah Kosong", "-")
        sub_graded["kunci_terpakai"] = graded_rec.get("Kunci Terpakai", "-")
        sub_graded["jawaban_terisi"] = graded_rec.get("Jawaban Terisi", "-")
        sub_graded["total_soal"] = graded_rec.get("Total Soal", 0)

        # 3-Way Comparison
        matched_sheet = graded_rec.get("Kunci Terpakai")
        kunci_map = kunci_sheets.get(matched_sheet, {})
        question_comparison = {}
        ai_data = sub.get("ai_analysis", {})

        for q_num, corr_opt in kunci_map.items():
            item_key = f"soal_{q_num}"
            item_key_alt = f"soal_{q_num:02d}"
            
            user_ans = sub.get("answers", {}).get(item_key, sub.get("answers", {}).get(item_key_alt, "BLANK"))
            ai_item = ai_data.get(item_key, ai_data.get(item_key_alt, {}))
            
            ai_pred = ai_item.get("ai_prediction", user_ans)
            ai_conf = ai_item.get("confidence", 95.0 if user_ans != "BLANK" else 0.0)
            
            allowed_opts = [x.strip().upper() for x in str(corr_opt).split(",")]
            is_student_match = str(user_ans).upper() in allowed_opts
            is_ai_match = str(ai_pred).upper() in allowed_opts
            is_blank = str(user_ans).upper() in ["BLANK", "?", "-", "", "NONE"]

            question_comparison[str(q_num)] = {
                "question_no": q_num,
                "student_ans": user_ans if not is_blank else "-",
                "ai_prediction": ai_pred if ai_pred != "BLANK" else "-",
                "ai_confidence": ai_conf,
                "official_key": corr_opt,
                "is_correct": is_student_match,
                "ai_agrees_with_key": is_ai_match,
                "status": "correct" if is_student_match else ("blank" if is_blank else "wrong")
            }

        sub_graded["question_comparison"] = question_comparison

        val_str = str(sub_graded["nilai"])
        try:
            val_f = float(val_str)
            scores.append(val_f)
        except ValueError:
            pass

        fak = sub.get("fakultas", "Lainnya")
        fakultas_counts[fak] = fakultas_counts.get(fak, 0) + 1

        graded_list.append(sub_graded)

    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
    highest_score = max(scores) if scores else 0.0
    lowest_score = min(scores) if scores else 0.0
    pass_count = sum(1 for s in scores if s >= 60.0)
    pass_rate = round((pass_count / len(scores)) * 100, 1) if scores else 0.0

    return {
        "total_graded": len(graded_list),
        "available_keys": list(kunci_sheets.keys()),
        "summary": {
            "avg_score": avg_score,
            "highest_score": highest_score,
            "lowest_score": lowest_score,
            "pass_rate": pass_rate,
            "total_students": len(graded_list),
            "faculty_distribution": fakultas_counts
        },
        "results": graded_list
    }


@app.post("/api/export/excel")
async def export_excel(payload: Dict[str, Any]):
    records = payload.get("results", [])
    if not records:
        raise HTTPException(status_code=400, detail="No records to export")

    rows = []
    for r in records:
        row = {
            "Nama Mahasiswa": r.get("nama", ""),
            "NPM": r.get("npm", ""),
            "Fakultas": r.get("fakultas", ""),
            "Kode Soal": r.get("kode_soal", ""),
            "Nilai Akhir": r.get("nilai", ""),
            "Benar": r.get("benar", ""),
            "Salah": r.get("salah", ""),
            "Kosong": r.get("kosong", ""),
            "Kunci Terpakai": r.get("kunci_terpakai", ""),
            "Status Scan": r.get("status", ""),
            "Nama Berkas": r.get("filename", "")
        }
        for q_idx in range(1, 101):
            k = f"soal_{q_idx}"
            if k in r.get("answers", {}):
                row[f"No {q_idx:02d}"] = r["answers"][k]
        rows.append(row)

    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Hasil Penilaian LJK")

    buf.seek(0)
    return StreamingResponse, Response(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=Rekap_Nilai_LJK_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"}
    )


@app.get("/api/sample-key")
@app.head("/api/sample-key")
def download_sample_key():
    sample_buf = generate_sample_kunci_excel()
    return Response(
        content=sample_buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=Template_Kunci_Jawaban_Sample.xlsx",
            "Content-Length": str(len(sample_buf))
        }
    )


os.makedirs(os.path.join(BASE_DIR, "static"), exist_ok=True)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
def serve_index():
    index_path = os.path.join(BASE_DIR, "static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>OMR Assessment System Frontend Active</h1>")
