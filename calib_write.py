import cv2, numpy as np, json

SRC='/opt/data/omr_fe/test_landscape_correct.png'
TMPL='/opt/data/omr_fe/template_landscape.json'

img = cv2.imread(SRC)
g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
_,bw = cv2.threshold(g,160,255,cv2.THRESH_BINARY_INV)
horiz = cv2.morphologyEx(bw,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(25,1)))
vert  = cv2.morphologyEx(bw,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,25)))
grid  = cv2.bitwise_or(horiz,vert)
contours,_ = cv2.findContours(grid,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)

pts=[]
for c in contours:
    x,y,w,h=cv2.boundingRect(c)
    if w>30 and h>20 and h<60 and w<80:
        pts.append((int(x+w/2),int(y+h/2)))
pts=sorted(pts,key=lambda p:p[1])

# cluster into 15 rows, then 20 cx per row
rows=[]
for cx,cy in pts:
    for r in rows:
        if abs(r['y']-cy)<25:
            r['pts'].append((cx,cy)); break
    else:
        rows.append({'y':cy,'pts':[(cx,cy)]})
rows.sort(key=lambda r:r['y'])

def cluster_cx(pxs,thr=40):
    grp=[[pxs[0]]]
    for px in pxs[1:]:
        if px-grp[-1][-1]>thr: grp.append([px])
        else: grp[-1].append(px)
    return [round(sum(x)/len(x)) for x in grp]

# average centers per column across rows (col -> 4 box centers)
col_centers=[]  # 5 cols x 4
col_boxes=[[] for _ in range(5)]
for r in rows:
    cc=cluster_cx(sorted(p[0] for p in r['pts']))
    if len(cc)!=20:
        print('WARN row y',r['y'],'has',len(cc),'centers, skip'); continue
    for ci in range(5):
        col_boxes[ci].append(cc[ci*4:(ci+1)*4])

# average box centers per col
for ci in range(5):
    arr=np.array(col_boxes[ci])  # Nx4
    avg=np.round(arr.mean(axis=0)).astype(int).tolist()
    col_centers.append(avg)
    print(f'Col{ci+1} centers={avg}')

# y centers: average per row
y_arr=np.round(np.mean([[r['y']] for r in rows],axis=0)).astype(int)
q_ys=[int(r['y']) for r in rows]

# Build template (preserve existing bubble size/geometry from current template)
t=json.load(open(TMPL))
fields=t['fields']
col_names=list(fields.keys())
# current item template already has right structure; just override cx (and cy)
for ci,name in enumerate(col_names):
    boxc=col_centers[ci]  # [A,B,C,D] avg x
    f=fields[name]
    for i,it in enumerate(f['items']):
        qy=q_ys[i]
        for bi,b in enumerate(it['bubbles']):
            b['cx']=float(boxc[bi])
            b['cy']=float(qy)
        # keep w/h/radius/shape from original (loaded fresh)

json.dump(t, open(TMPL,'w'), indent=2)
print('template_landscape.json updated with calibrated centers')
