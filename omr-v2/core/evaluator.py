import pandas as pd
import re
import io

def parse_kunci_jawaban_excel(file_source):
    """
    Parses an Excel file containing answer keys per sheet.
    Sheet names correspond to Kode Soal (e.g. 'kj048', 'kj123', '048').
    Each sheet contains 2 columns:
      Col 0: Question number (1, 2, ..., 75)
      Col 1: Key answer option ('A', 'B', 'C', 'D', 'E')
    Returns:
      dict: {sheet_name: {1: 'A', 2: 'B', ...}}
    """
    xls = pd.ExcelFile(file_source)
    sheet_keys = {}
    
    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet, header=None)
        q_dict = {}
        for _, row in df.iterrows():
            if len(row) < 2 or pd.isna(row.iloc[0]) or pd.isna(row.iloc[1]):
                continue
            v0 = str(row.iloc[0]).strip()
            v1 = str(row.iloc[1]).strip().upper()
            
            # Find question number from first column (e.g. '1', '1.0', 'soal_1')
            m = re.search(r'\d+', v0)
            if m:
                q_num = int(m.group())
                # Extract valid answer options (A, B, C, D, E)
                opts = re.findall(r'[A-E]', v1)
                if opts:
                    q_dict[q_num] = ','.join(opts) if len(opts) > 1 else opts[0]
        if q_dict:
            sheet_keys[sheet] = q_dict
            
    return sheet_keys


def find_matching_kunci_sheet(kode_soal, sheet_names):
    """
    Finds the sheet name corresponding to student's Kode Soal.
    Matches e.g. kode_soal='048' with 'kj048', 'KJ048', 'kj_048', '048', etc.
    """
    if not sheet_names:
        return None
    if not kode_soal or str(kode_soal).strip() in ["-", ""]:
        return sheet_names[0] if len(sheet_names) == 1 else None
        
    cleaned_kode = str(kode_soal).strip().lower()
    digits_only = re.sub(r'\D', '', cleaned_kode)
    int_str = ""
    padded_str = ""
    if digits_only:
        int_val = int(digits_only)
        int_str = str(int_val)
        padded_str = f"{int_val:03d}"
        
    candidates = [
        f"kj{cleaned_kode}",
        f"kj{int_str}" if int_str else "",
        f"kj{padded_str}" if padded_str else "",
        cleaned_kode,
        int_str,
        padded_str
    ]
    candidates = [c for c in candidates if c]
    
    # 1. Exact match on normalized sheet names
    for sh in sheet_names:
        clean_sh = re.sub(r'[\s_\-]', '', sh.lower())
        for cand in candidates:
            clean_cand = re.sub(r'[\s_\-]', '', cand)
            if clean_sh == clean_cand:
                return sh
                
    # 2. Substring match
    for sh in sheet_names:
        clean_sh = re.sub(r'[\s_\-]', '', sh.lower())
        if int_str and int_str in clean_sh and "kj" in clean_sh:
            return sh
        if cleaned_kode and cleaned_kode in clean_sh:
            return sh
            
    # 3. Fallback if single sheet exists
    if len(sheet_names) == 1:
        return sheet_names[0]
    return None


def grade_student_record(student_record, all_kunci_sheets):
    """
    Grades a single student record against the appropriate answer key sheet.
    Calculates:
      - Nilai (0 - 100)
      - Jumlah Benar
      - Jumlah Salah
      - Jumlah Kosong
      - Kunci Terpakai
    """
    kode_soal = student_record.get("Kode Soal", "").strip()
    sheet_names = list(all_kunci_sheets.keys())
    matched_sheet = find_matching_kunci_sheet(kode_soal, sheet_names)
    
    if not matched_sheet or matched_sheet not in all_kunci_sheets:
        student_record["Nilai"] = "-"
        student_record["Jumlah Benar"] = "-"
        student_record["Jumlah Salah"] = "-"
        student_record["Jumlah Kosong"] = "-"
        student_record["Kunci Terpakai"] = "Tidak Ditemukan"
        return student_record
        
    kunci_dict = all_kunci_sheets[matched_sheet]
    total_soal = len(kunci_dict)
    if total_soal == 0:
        student_record["Nilai"] = "-"
        student_record["Jumlah Benar"] = "-"
        student_record["Jumlah Salah"] = "-"
        student_record["Jumlah Kosong"] = "-"
        student_record["Kunci Terpakai"] = matched_sheet
        return student_record
        
    benar = 0
    salah = 0
    kosong = 0
    
    for q_num, correct_opt in kunci_dict.items():
        # Look for student answer with pad_zero (soal_01) or no-pad (soal_1)
        ans = student_record.get(f"soal_{q_num:02d}", student_record.get(f"soal_{q_num}", "BLANK"))
        if ans in ["BLANK", "?", None, ""]:
            kosong += 1
        else:
            allowed_opts = [x.strip() for x in correct_opt.split(",")]
            if ans in allowed_opts:
                benar += 1
            else:
                salah += 1
                
    skor = round((benar / total_soal) * 100, 1)
    skor_str = f"{skor:.1f}" if skor != int(skor) else f"{int(skor)}"
    
    student_record["Nilai"] = skor_str
    student_record["Jumlah Benar"] = benar
    student_record["Jumlah Salah"] = salah
    student_record["Jumlah Kosong"] = kosong
    student_record["Kunci Terpakai"] = matched_sheet
    return student_record


def generate_sample_kunci_excel():
    """Generates an in-memory sample Excel file containing answer keys for kj048 and kj123."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        options = ['A', 'B', 'C', 'D']
        # Sheet kj048: 75 questions
        rows_048 = [[i, options[(i - 1) % 4]] for i in range(1, 76)]
        pd.DataFrame(rows_048).to_excel(writer, sheet_name='kj048', index=False, header=False)
        
        # Sheet kj123: 75 questions
        rows_123 = [[i, options[((i * 2) - 1) % 4]] for i in range(1, 76)]
        pd.DataFrame(rows_123).to_excel(writer, sheet_name='kj123', index=False, header=False)
    buf.seek(0)
    return buf.getvalue()
