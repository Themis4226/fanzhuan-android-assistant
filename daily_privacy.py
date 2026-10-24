"""Reject notification overlays before saving task screenshots or sending OCR."""
import re

def assert_no_notification(view):
    for element in view['elements']:
        if not 100<element['center'][1]<500:
            continue
        text=element['text'].strip()
        if text in {'刚刚','现在','QQ','通知'} or re.fullmatch(r'微信(?:\s*[（(]分身[）)])?',text):
            raise RuntimeError('Notification overlay detected; wait for it to disappear. No task upload or tap performed.')
