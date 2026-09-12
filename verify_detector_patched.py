import cv2, numpy as np, sys
sys.path.insert(0,'/opt/data/omr_fe')
from core.decoder import decode_field_detailed

SRC='/opt/data/profiles/developer/cache/images/img_4c545cfcf69c.jpg'
img=cv2.imread(SRC)
g=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)

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
row_centers=[sorted(set(cluster_centers(r['pts'])))[:20] for r in rows]
q_ys=[r['y'] for r in rows]

col_names=['Soal-A','Soal-B','Soal-C','Soal-D','Soal-E']
fields={}
for ci,name in enumerate(col_names):
    items=[]
    for qi in range(15):
        centers=row_centers[qi] if len(row_centers[qi])>=20 else list(row_centers[qi])+[0]*(20-len(row_centers[qi]))
        blk=centers[ci*4:(ci+1)*4]
        bubbles=[{'option':o,'cx':float(blk[i]),'cy':float(q_ys[qi]),'w':34,'h':34,'shape':'square'} for i,o in enumerate(['A','B','C','D'])]
        items.append({'name':f'soal_{qi+1:02d}','index':qi+1,'bubbles':bubbles})
    fields[name]={'field_type':'multiple_choice','field_name':name,'num_questions':15,'items':items}

answers={}
for ci,name in enumerate(col_names):
    vals,ana=decode_field_detailed(g,fields[name],thresh=0.28,margin=0.08)
    for it in fields[name]['items']:
        q=ci*15+it['index']
        answers[q]=vals.get(it['name'],'?')

clean=sum(1 for v in answers.values() if v in 'ABCD')
mult=sum(1 for v in answers.values() if v=='MULTIPLE')
blank=sum(1 for v in answers.values() if v=='BLANK')
other=len(answers)-clean-mult-blank
print(f'clean={clean} mult={mult} blank={blank} other={other} / {len(answers)}')
for q in range(1,76):
    print(f'{q:2d}: {answers[q]}')
