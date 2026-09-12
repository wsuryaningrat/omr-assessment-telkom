import cv2, numpy as np, json

img = cv2.imread('/opt/data/omr_fe/test_landscape_correct.png')
g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# Blank-border detection: find horizontal+vertical lines -> boxes.
# 1) threshold
_, bw = cv2.threshold(g, 160, 255, cv2.THRESH_BINARY_INV)

# Morphological to keep box borders: long horizontal & vertical lines.
horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT,(25,1)))
vert  = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT,(1,25)))
grid = cv2.bitwise_or(horiz, vert)

# Contours of boxes (they are hollow squares)
contours,_ = cv2.findContours(grid, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
boxes=[]
for c in contours:
    x,y,w,h = cv2.boundingRect(c)
    if w>30 and h>20 and h<60 and w<80:  # question box size
        boxes.append((int(x+w/2), int(y+h/2)))  # centroid
print('detected question boxes:', len(boxes))

# Question y-lines (15): cluster cy
ys = sorted(set(round(y/3)*3 for _,y in boxes))
# actually group by y proximity
from collections import defaultdict
rows=defaultdict(list)
for cx,cy in boxes:
    rows[round(cy/4)*4].append(cx)
row_ys = sorted(rows.keys())
print('num row clusters:', len(row_ys))
# for first row cluster, sort cx -> 4*5=20 centers expected
first = rows[row_ys[0]]
first.sort()
print('row0 centers (cx):', first[:24])
print('row0 y', row_ys[0], 'row1 y', row_ys[1] if len(row_ys)>1 else '-')
