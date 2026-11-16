"""v2.16 EXPERIMENT (not in the app): tiled full-resolution WireNet inference vs the shipped 1024 px letterbox.

Overlapping 1024 px tiles (192 px overlap, linear blend) with the mirrored pass, at the photo's own resolution, against
the shipped single pass on the photo shrunk to 1024 px. No high-resolution labels exist (20 of 22 validation photos are
smaller than 1024 px and were enlarged), so this is a visual comparison on the 2048 px field photos only:
writes <name>_tiled_vs_1024.jpg (green = tiled only, red = 1024 only, cyan = both) and prints pixel counts.

    python -m ml.experiments.v216_tiled street1 street2 (output: runs/v216/tiled, or $V216_TILED_OUT)
"""
import os
import sys
import time

import cv2
import numpy as np

from app.core.config import ROOT_DIR, load_analysis_config
from app.pipeline.wirenet import make_segmenter, predict_probs

SP=os.environ.get('V216_TILED_OUT','runs/v216/tiled/'); os.makedirs(SP, exist_ok=True)
cfg=load_analysis_config(ROOT_DIR/"config"/"wireframe.yaml"); seg=make_segmenter(cfg.segmentation,ROOT_DIR); seg.load()
net,dev=seg._net,seg._device
def tiled(rgb,T=1024,ov=192):
    h,w=rgb.shape[:2]; acc=np.zeros((5,h,w),np.float32); wt=np.zeros((h,w),np.float32)
    ramp=np.minimum(np.arange(T)+1, np.arange(T)[::-1]+1).astype(np.float32); ramp=np.minimum(ramp/ov,1)
    win=np.outer(ramp,ramp)
    ys=list(range(0,max(h-T,0)+1,T-ov)); xs=list(range(0,max(w-T,0)+1,T-ov))
    if ys[-1]+T<h: ys.append(h-T)
    if xs[-1]+T<w: xs.append(w-T)
    for y in ys:
        for x in xs:
            t=rgb[y:y+T,x:x+T]; p=predict_probs(net,t,dev); p2=predict_probs(net,np.ascontiguousarray(t[:,::-1]),dev)[:,:,::-1]; p=(p+p2)/2
            ww=win[:t.shape[0],:t.shape[1]]; acc[:,y:y+T,x:x+T]+=p*ww; wt[y:y+T,x:x+T]+=ww
    return acc/wt
for n in sys.argv[1:]:
    rgb=cv2.cvtColor(cv2.imread(f'runs/v216/regression/{n}.jpg'),cv2.COLOR_BGR2RGB)
    t=time.time(); P=tiled(rgb); dt=time.time()-t
    small=cv2.resize(rgb,(1024,768),interpolation=cv2.INTER_AREA)
    q=predict_probs(net,small,dev); q=(q+predict_probs(net,np.ascontiguousarray(small[:,::-1]),dev)[:,:,::-1])/2
    Q=np.stack([cv2.resize(c,(rgb.shape[1],rgb.shape[0])) for c in q])
    W=[2,3,4]
    a=np.isin(P.argmax(0),W); b=np.isin(Q.argmax(0),W)
    print(n,'tiled s',round(dt,1),'wire px tiled',int(a.sum()),'1024',int(b.sum()),'agree',round((a&b).sum()/max((a|b).sum(),1),3))
    vis=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR)
    out=vis.copy(); out[b&~a]=(0,0,255); out[a&~b]=(0,255,0); out[a&b]=(255,255,0)
    crop=out[200:900, 700:1700]; cv2.imwrite(SP+f'{n}_tiled_vs_1024.jpg',crop)
