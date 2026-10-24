"""Reuse OCR only for byte-identical fresh screenshots, within one process."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import time

from phone_capture import timestamp


class ExactFrameObserver:
    """The caller still captures and checks foreground on every read.

    No fuzzy image comparison or coordinate scaling: a changed pixel/PNG byte,
    ROI, or orientation setting requires OCR again. Returned views are isolated
    so a request builder cannot change the cached OCR. Nothing is stored on disk.
    """

    def __init__(self, engine, observe_fn, *, log=None, enabled=True, capacity=2):
        self.engine = engine
        self.observe_fn = observe_fn
        self.log = log
        self.enabled = enabled
        self.capacity = capacity
        self._frames = OrderedDict()

    def __call__(self, data, roi=None, *, live=False):
        started = time.perf_counter()
        key = (hashlib.sha256(data).digest(), tuple(roi) if roi is not None else None,
               getattr(self.engine, 'use_cls', None))
        cached = self.enabled and key in self._frames
        if cached:
            view = deepcopy(self._frames[key])
            self._frames.move_to_end(key)
            view['observed_at'] = timestamp()
            view['live_capture'] = live
        else:
            view = self.observe_fn(data, roi, engine=self.engine, live=live)
            if self.enabled:
                self._frames[key] = deepcopy(view)
                while len(self._frames) > self.capacity:
                    self._frames.popitem(last=False)
        if self.log is not None:
            self.log.event('ocr_observation', cached=bool(cached),
                           elapsed_ms=round((time.perf_counter()-started)*1000, 2),
                           text_regions=len(view['elements']))
        return view
