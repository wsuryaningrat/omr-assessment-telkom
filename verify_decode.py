import cv2, sys, json
sys.path.insert(0, '.')
from core.decoder import decode_field_detailed

img = cv2.imread('test_landscape_correct.png')
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
t = json.load(open('template_landscape.json'))
fields = t['fields']

# decode per column, map soal number -> answer
answers = {}
detail = {}
for key, field in fields.items():
    vals, ana = decode_field_detailed(gray, field, thresh=0.28, margin=0.08)
    col = field.get('field_name', key)
    # 'Soal-A'..'Soal-E' -> 0..4 -> global start 1,16,31,46,61
    col_idx = ord(col[-1].upper()) - ord('A')
    start = 1 + 15 * col_idx
    # item name is soal_NN -> use item index instead
    items = field.get('items', [])
    for it in items:
        q = it.get('index')
        global_q = start + (q - 1)
        answers[global_q] = vals.get(it.get('name'), '?')
        detail[global_q] = ana.get(it.get('name'), {})

# also expose confidence summary
print("=== Jawaban terbaca per soal (A/B/C/D) ===")
for q in sorted(answers):
    a = answers[q]
    c = detail.get(q, {}).get('confidence', 0)
    print(f"{q:2d}: {a}  (conf {c:.0f}%)")
