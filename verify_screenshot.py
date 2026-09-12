import cv2, numpy as np, json, sys
sys.path.insert(0, '/opt/data/omr_fe')
from core.decoder import decode_field_detailed
from core.detector import calculate_fill_ratio

SRC='/opt/data/profiles/developer/cache/images/img_4c545cfcf69c.jpg'
img=cv2.imread(SRC)
g=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
h,w=g.shape

# ---- calibrate box centers from actual grid ----
_,bw=cv2.threshold(g,160,255,cv2.THRESH_BINARY_INV)
horiz=cv2.morphologyEx(bw,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(25,1)))
vert=cv2.morphologyEx(bw,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,25)))
grid=cv2.bitwise_or(horiz,vert)
cont,_=cv2.findContours(grid,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
pts=[]
for c in cont:
    x,y,w2,h2=cv2.boundingRect(c)
    if w2>25 and h2>15 and h2<60 and w2<90:
        pts.append((int(x+w2/2),int(y+h2/2)))

pts=sorted(pts,key=lambda p:p[1])
rows=[]
for cx,cy in pts:
    for r in rows:
        if abs(r['y']-cy)<20: r['pts'].append(cx); break
    else: rows.append({'y':cy,'pts':[cx]})
rows.sort(key=lambda r:r['y'])
rows=rows[:15]

def cluster_centers(cxs,thr=25):
    cxs=sorted(cxs); grp=[[cxs[0]]]
    for v in cxs[1:]:
        if v-grp[-1][-1]>thr: grp.append([v])
        else: grp[-1].append(v)
    return [round(np.mean(x)) for x in grp]

# per row: get 20 unique centers (5 cols x 4), dedupe via clustering
row_centers=[]
for r in rows:
    cc=cluster_centers(r['pts'])
    # dedupe exact double -> unique sorted
    uni=sorted(set(cc))
    row_centers.append(uni)
    # take first unique 20
q_ys=[r['y'] for r in rows]

# expect each row 20 centers
print('rows:',len(rows),'centers per row (first):',len(row_centers[0]))
col_names=['Soal-A','Soal-B','Soal-C','Soal-D','Soal-E']

# Build 5 fields; column j centers are row_centers[i][4j:4j+4]
fields={}
for ci,name in enumerate(col_names):
    items=[]
    for qi in range(15):
        # column ci center block
        centers=row_centers[qi]
        if len(centers)<20: centers=centers+[0]*(20-len(centers))
        blk=centers[ci*4:(ci+1)*4]
        bubbles=[]
        for oi,opt in enumerate(['A','B','C','D']):
            bubbles.append({'option':opt,'cx':float(blk[oi]),'cy':float(q_ys[qi]),'w':30,'h':30,'shape':'square'})
        items.append({'name':f'soal_{qi+1:02d}','index':qi+1,'bubbles':bubbles})
    fields[name]={'field_type':'multiple_choice','field_name':name,'num_questions':15,'roi':{},'items':items}

# ---- decode ----
paper_bg=float(np.percentile(g,92)); 
if paper_bg<150: paper_bg=240.0
answers={}
scores=[]
for ci,name in enumerate(col_names):
    vals,ana=decode_field_detailed(g,fields[name],thresh=0.2,margin=0.06)
    items=fields[name]['items']
    for it in items:
        q=ci*15+it['index']
        answers[q]=vals.get(it['name'],'?')
        a=ana.get(it['name'],{})
        s=a.get('scores',{})
        scores.append((q,s))

print('=== Decoded answers per soal ===')
for q in sorted(answers):
    print(f'{q:2d}: {answers[q]}')
