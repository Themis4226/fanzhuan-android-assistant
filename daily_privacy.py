"""Reject notification overlays before saving task screenshots or sending OCR."""
import re
import time


class NotificationOverlay(RuntimeError):
    pass

def assert_no_notification(view):
    for element in view['elements']:
        if not 100<element['center'][1]<500:
            continue
        text=element['text'].strip()
        if text in {'刚刚','现在','QQ','通知'} or re.fullmatch(r'微信(?:\s*[（(]分身[）)])?',text):
            raise NotificationOverlay('Notification overlay detected; wait for it to disappear. No task upload or tap performed.')


def notification_free_frame(capture,recognize,log,timeout=15):
    """Discard banner frames in memory; wait without input, uploads or persistence."""
    deadline=time.monotonic()+timeout
    waiting=False
    while True:
        raw=capture();view=recognize(raw)
        try:
            assert_no_notification(view)
        except NotificationOverlay:
            if not waiting:
                print('检测到通知横幅，暂不保存、上传或点击，等待其消失…',flush=True)
                log.event('notification_wait_started',api_calls=0,phone_actions=0)
                waiting=True
            if time.monotonic()>=deadline:
                raise NotificationOverlay('通知持续遮挡超过15秒，已暂停；未保存或发送通知内容。')
            time.sleep(1)
            continue
        if waiting:log.event('notification_cleared',api_calls=0,phone_actions=0)
        return raw,view
