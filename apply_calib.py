import json, numpy as np

# Load current template
t = json.load(open('/home/bagas/projects/active/omr-assessment/template-final.json'))

# Detected y positions (median across columns, interpolated to 15 rows)
detected_ys = [
    1690,  # row 0
    1771,  # row 1
    1853,  # row 2
    1935,  # row 3
    2017,  # row 4
    2100,  # row 5
    2182,  # row 6
    2264,  # row 7
    2351,  # row 8
    2392,  # row 9 (interpolated from template gap)
    2434,  # row 10
    2476,  # row 11
    2518,  # row 12
    2560,  # row 13
    2602,  # row 14
]

# But template only goes to 2364 - let's use actual template row count
# Template has 15 items per column, current y range: 1692-2364
# Detected has 9 rows at 1690-2351
# So detected rows 0-8 map to template rows 0-8
# Need to fill rows 9-14 by extending pattern (gap ~82)

# Actually, detected 9 rows in 1690-2351 span = 661px / 8 gaps = ~82.6px per row
# Template 15 rows in 1692-2364 span = 672px / 14 gaps = 48px per row
# Different scaling! The warp produces different vertical scale.

# Use detected row_ys for first 9 rows, then extrapolate
detected_row_ys = [1690, 1771, 1853, 1935, 2017, 2100, 2182, 2264, 2351]
gaps = [detected_row_ys[i+1]-detected_row_ys[i] for i in range(len(detected_row_ys)-1)]
avg_gap = sum(gaps)/len(gaps)
print(f"Avg detected gap: {avg_gap:.1f}")

# Extend to 15 rows
row_ys = detected_row_ys.copy()
for i in range(9, 15):
    row_ys.append(int(row_ys[-1] + avg_gap))
print(f"Final 15 row Ys: {row_ys}")

# X-centers per column (from detected, consistent across rows)
col_xs = {
    'Soal-A': [138, 223, 307, 392],  # A,B,C,D - estimate D from pattern
    'Soal-B': [477, 561, 645, 729],
    'Soal-C': [814, 898, 983, 1067],
    'Soal-D': [1152, 1236, 1320, 1404],
    'Soal-E': [1489, 1573, 1658, 1742],
}

# For each column, update all 15 items
for col_name, xs in col_xs.items():
    items = t['fields'][col_name]['items']
    for i in range(15):
        for j, opt in enumerate(['A','B','C','D']):
            items[i]['bubbles'][j]['cx'] = xs[j]
            items[i]['bubbles'][j]['cy'] = row_ys[i]
            # w/h keep from template
    print(f"{col_name}: updated 15 rows, Ys {row_ys[0]}-{row_ys[-1]}, Xs {xs}")

# Save
with open('/home/bagas/projects/active/omr-assessment/template-final.json', 'w') as f:
    json.dump(t, f, indent=2)

print("Template calibrated & saved.")