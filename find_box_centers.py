import cv2
import numpy as np
import json

img = cv2.imread('/opt/data/omr_fe/test_landscape_correct.png', cv2.IMREAD_GRAYSCALE)
h, w = img.shape
print(f"Image: {w}x{h}")

# Known y-centers for 15 question rows
question_ys = [150, 217, 284, 350, 416, 504, 572, 638, 705, 773, 862, 931, 997, 1063, 1131]

_, binary = cv2.threshold(img, 128, 1, cv2.THRESH_BINARY_INV)

# Step 1: Look at a full horizontal profile at several rows to understand layout
print("=== Horizontal profile at y=150 (first question row) ===")
row = binary[148:152, :]  # thin band
hprof = np.sum(row, axis=0)

# Print in chunks
for x in range(0, w, 20):
    end = min(x+20, w)
    vals = [str(int(v)) for v in hprof[x:end]]
    print(f"  x={x:4d}-{end:4d}: {' '.join(vals)}")

# Step 2: Find ALL vertical dark lines across the entire image
print("\n=== All significant vertical lines (appearing in >= 8 of 15 rows) ===")
y_top = question_ys[0] - 35
y_bot = question_ys[-1] + 35

# For each x, count across all row bands
vertical_profile = np.zeros(w, dtype=int)
for qy in question_ys:
    band = binary[max(0,qy-5):min(h,qy+5), :]
    has_dark = np.any(band > 0, axis=0).astype(int)
    vertical_profile += has_dark

# Find peaks (positions with high counts = consistent dark columns)
significant = np.where(vertical_profile >= 8)[0]
print(f"Positions with count >= 8: {len(significant)} positions")

# Group consecutive positions
groups = []
if len(significant) > 0:
    start = significant[0]
    prev = significant[0]
    for x in significant[1:]:
        if x - prev > 3:  # gap > 3px = new group
            groups.append((start, prev))
            start = x
        prev = x
    groups.append((start, prev))

print(f"Grouped into {len(groups)} vertical features:")
for s, e in groups:
    mid = (s + e) // 2
    peak_val = max(vertical_profile[s:e+1])
    width = e - s + 1
    print(f"  x={s}-{e} (center={mid}, width={width}, max_count={peak_val})")

# Step 3: Identify which are box edges vs text/number columns
# Box edges should be thin (1-5px) and appear at all 15 rows with count=15
# Number/text columns should be wider and have lower count
print("\n=== Feature classification ===")
box_edges = []  # thin features with high count
text_cols = []  # wider features
for s, e in groups:
    mid = (s + e) // 2
    peak_val = int(max(vertical_profile[s:e+1]))
    width = e - s + 1
    if width <= 8 and peak_val >= 14:
        box_edges.append(mid)
        print(f"  BOX EDGE: x={mid} (width={width}, count={peak_val})")
    elif width <= 8 and peak_val >= 12:
        box_edges.append(mid)
        print(f"  POSSIBLE BOX EDGE: x={mid} (width={width}, count={peak_val})")
    else:
        text_cols.append((s, e))
        print(f"  TEXT/NUMBER: x={s}-{e} (width={width}, count={peak_val})")

print(f"\nAll box edges: {box_edges}")

# Step 4: Group box edges into columns - edges that are close together 
# form the boundaries of boxes within a column
# Expected: 5 columns, each with 5 edges (left border + 4 box separators + right border)
# = 25 edges total, or maybe 20 if some borders are shared

# Try to cluster edges into groups of ~5
print("\n=== Clustering edges into columns ===")
if box_edges:
    clusters = [[box_edges[0]]]
    for x in box_edges[1:]:
        if x - clusters[-1][-1] < 50:  # within same column
            clusters[-1].append(x)
        else:
            clusters.append([x])
    
    print(f"Found {len(clusters)} edge clusters:")
    for i, c in enumerate(clusters):
        print(f"  Cluster {i}: {c} (spans x={c[0]}-{c[-1]}, {len(c)} edges)")
        if len(c) >= 2:
            for j in range(len(c)-1):
                gap = c[j+1] - c[j]
                print(f"    gap between edges {j} and {j+1}: {gap}px")

# Step 5: For each row, also print the horizontal profile to verify
print("\n=== Row profile at y=150, all x positions with dark pixels ===")
band = binary[148:152, :]
row_has_dark = np.any(band > 0, axis=0)
dark_positions = np.where(row_has_dark)[0]
# Group consecutive
dark_groups = []
if len(dark_positions) > 0:
    start = dark_positions[0]
    prev = dark_positions[0]
    for x in dark_positions[1:]:
        if x - prev > 2:
            dark_groups.append((start, prev))
            start = x
        prev = x
    dark_groups.append((start, prev))

print(f"Dark pixel groups: {len(dark_groups)}")
for s, e in dark_groups:
    print(f"  x={s}-{e} (center={(s+e)//2}, width={e-s+1})")
