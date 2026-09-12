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

print('Template Soal-A row 0:')
for b in items[0]['bubbles']:
    cx, cy = int(b['cx']), int(b['cy'])
    patch = g[cy-20:cy+20, cx-20:cx+20]
    if patch.size > 0:
        mean_val = np.mean(patch)
        min_val = np.min(patch)
        print('  %s cx=%d cy=%d: mean=%.1f min=%d' % (b['option'], cx, cy, mean_val, min_val))

# Also check at detected x centers (138,223,307,392) for row 0
print('\nDetected x-centers for row 0 (cy=1692):')
for cx in [138, 223, 307, 392]:
    patch = g[1672:1712, cx-20:cx+20]
    if patch.size > 0:
        mean_val = np.mean(patch)
        min_val = np.min(patch)
        print('  cx=%d cy=1692: mean=%.1f min=%d' % (cx, mean_val, min_val))