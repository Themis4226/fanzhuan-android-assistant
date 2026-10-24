"""Offline OCR benchmark only: no USB, API calls, or screenshot uploads."""
import argparse
import json
from pathlib import Path
import statistics
import time

from fast_observer import ExactFrameObserver
from ocr_view import demo_png, load_engine, observe


def signature(view):
    return [(e['text'],e['confidence'],e['bounds'],e['center']) for e in view['elements']]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image',type=Path,action='append',help='Local task PNG; never uploaded')
    p.add_argument('--repeat',type=int,default=3)
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    if not 2<=a.repeat<=20:p.error('repeat must be 2..20')
    if a.output and a.output.exists():p.error('output exists; choose a new file')
    frames=[f.read_bytes() for f in a.image] if a.image else [demo_png()]
    engine=load_engine();engine.use_cls=False
    observe(frames[0],engine=engine)  # Lazy model load/warm-up excluded equally.
    baseline=[];optimized=[];matches=True;hit_times=[]
    for raw in frames:
        cached=ExactFrameObserver(engine,observe)
        expected=None
        for _ in range(a.repeat):
            t=time.perf_counter();v=observe(raw,engine=engine);baseline.append(time.perf_counter()-t)
            expected=signature(v)
        for i in range(a.repeat):
            t=time.perf_counter();v=cached(raw);dt=time.perf_counter()-t;optimized.append(dt)
            if i:hit_times.append(dt)
            matches &= signature(v)==expected
    result={'scope':'offline repeated-frame OCR, excluding USB, waits, and model latency',
            'frames':len(frames),'repeat_per_frame':a.repeat,'api_calls':0,'phone_actions':0,
            'ocr_text_confidence_and_coordinates_identical':matches,
            'baseline_total_seconds':round(sum(baseline),4),
            'optimized_total_seconds':round(sum(optimized),4),
            'speedup_for_this_workload':round(sum(baseline)/sum(optimized),2),
            'cache_hit_median_ms':round(statistics.median(hit_times)*1000,3)}
    if not matches:raise RuntimeError('OCR output mismatch')
    if a.output:
        with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
