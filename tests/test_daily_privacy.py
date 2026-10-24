import unittest
from daily_privacy import assert_no_notification

class NotificationPrivacyTests(unittest.TestCase):
    def test_banner_is_rejected_before_persistence_or_upload(self):
        for text in ('刚刚','微信(分身)','微信（分身）','微信'):
            with self.assertRaises(RuntimeError):
                assert_no_notification({'elements':[{'text':text,'center':[300,199]}]})

    def test_task_content_below_notification_area_is_accepted(self):
        assert_no_notification({'elements':[{'text':'今日任务','center':[540,175]},
                                           {'text':'微信','center':[500,1200]}]})

if __name__=='__main__':unittest.main()
