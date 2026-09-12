import cv2, numpy as np

SRC='/opt/data/profiles/developer/cache/images/img_4c545cfcf69c.jpg'
img=cv2.imread(SRC)
g=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
paper_bg=float(np.percentile(g,92))

# calibrated grid
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

def ratio_core(cx,cy,box=38,frac=0.5):
    x1=int(cx-box*frac/2); x2=int(cx+box*frac/2)
    y1=int(cy-box*frac/2); y2=int(cy+box*frac/2)
    p=g[y1:y2,x1:x2]
    return float((p<150).mean())

for frac in [0.3,0.4,0.5,0.6,0.7]:
    clean=0; mult=0; blank=0; seq=[]
    for qi in range(15):
        for ci in range(5):
            blk=row_centers[qi][ci*4:(ci+1)*4]
            ratios=[ratio_core(c,q_ys[qi],frac=frac) for c in blk]
            mx=max(ratios); snd=sorted(ratios,reverse=True)[1]
            # decide: if winner clearly above rest and above baseline
            if mx>0.35 and (mx-snd)>0.08:
                ans=chr(65+int(np.argmax(ratios))); clean+=1; seq.append(ans)
            elif mx<0.25:
                blank+=1
            else:
                mult+=1
    print(f'frac={frac}: clean={clean}/75  mult={mult} blank={blank}')
    if frac==0.45:
        # print sequence for this frac actually let's test 0.45 too
        pass
