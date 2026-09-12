import cv2, numpy as np, json, fitz
from core.alignment import detect_corners_and_crop

doc = fitz.open('/app/user_LJK_filled.pdf')
page = doc[0]
pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
arr = np.frombuffer(pix.tobytes('png'), np.uint8)
bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
warped, *_ = detect_corners_and_crop(bgr)
g = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)

t = json.load(open('/app/template-final.json'))

for col_name in ['Soal-A', 'Soal-B', 'Soal-C', 'Soal-D', 'Soal-E']:
    fdef = t['fields'][col_name]
    items = fdef['items']
    xs = [b['cx'] for b in items[0]['bubbles']]
    x_min = int(min(xs)) - 80
    x_max = int(max(xs)) + 80
    y_vals = [b['cy'] for it in items for b in it['bubbles']]
    y_min = int(min(y_vals)) - 80
    y_max = int(max(y_vals)) + 80

    sub = g[y_min:y_max, x_min:x_max]
    _, bw = cv2.threshold(sub, 120, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if 200 < area < 5000 and 0.5 < w/max(h,1) < 2.0:
            boxes.append((x_min + x + w//2, y_min + y + h//2))
    boxes.sort(key=lambda b: (b[1], b[0]))

    rows = []
    for b in boxes:
        if not rows or abs(b[1] - rows[-1][0][1]) > 18:
            rows.append([b])
        else:
            rows[-1].append(b)

    print('%s: %d rows (thresh 120)' % (col_name, len(rows)))
    for i, row in enumerate(rows[:15]):
        y_avg = sum(b[1] for b in row) / len(row)
        xs_r = sorted([b[0] for b in row])
        print('  row %d: y=%d n=%d xs=%s' % (i, int(y_avg), len(row), [int(x) for x in xs_r]))