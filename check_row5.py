import cv2, numpy as np, json, fitz
from core.alignment import detect_corners_and_crop

doc = fitz.open('/app/user_LJK_filled.pdf')
page = doc[0]
pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
arr = np.frombuffer(pix.tobytes('png'), np.uint8)
bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
warped, *_ = detect_corners_and_crop(bgr)
g = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)

t = json.load(open('template-final.json'))
items = t['fields']['Soal-A']['items']

# Row 5 (soal_06) - this gave C in scan
cy = items[5]['bubbles'][0]['cy']
print('Row 5 cy=%d:' % cy)
for b in items[5]['bubbles']:
    cx = int(b['cx'])
    patch = g[cy-20:cy+20, cx-20:cx+20]
    if patch.size > 0:
        mean_val = np.mean(patch)
        min_val = np.min(patch)
        ink = np.sum(patch < 150) / patch.size
        print('  %s cx=%d: mean=%.1f min=%d ink=%.3f' % (b['option'], cx, mean_val, min_val, ink))