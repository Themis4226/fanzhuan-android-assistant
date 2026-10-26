import unittest
from unittest.mock import Mock,patch
from daily_privacy import assert_no_notification,notification_free_frame,NotificationOverlay

class NotificationPrivacyTests(unittest.TestCase):
    def test_transient_notification_is_discarded_then_clean_frame_returned(self):
        private={'elements':[{'text':'刚刚','center':[300,199]},{'text':'SYNTHETIC_PRIVATE_BANNER','center':[300,250]}]}
        clean={'elements':[{'text':'今日任务','center':[540,175]}]}
        capture=Mock(side_effect=[b'private_frame',b'clean_frame']);recognize=Mock(side_effect=[private,clean]);log=Mock()
        with patch('daily_privacy.time.sleep'):
            raw,view=notification_free_frame(capture,recognize,log)
        self.assertEqual(raw,b'clean_frame');self.assertEqual(view,clean)
        self.assertEqual(capture.call_count,2)
        self.assertNotIn('SYNTHETIC_PRIVATE_BANNER',str(log.mock_calls))
        self.assertNotIn('private_frame',str(log.mock_calls))

    def test_persistent_notification_times_out_without_returning_a_frame(self):
        banner={'elements':[{'text':'通知','center':[300,199]}]};capture=Mock(return_value=b'private')
        with patch('daily_privacy.time.monotonic',side_effect=[0,16]),self.assertRaises(NotificationOverlay):
            notification_free_frame(capture,Mock(return_value=banner),Mock())
        self.assertEqual(capture.call_count,1)

    def test_non_notification_error_is_not_retried(self):
        capture=Mock(side_effect=RuntimeError('USB disconnected'))
        with self.assertRaisesRegex(RuntimeError,'USB disconnected'):
            notification_free_frame(capture,Mock(),Mock())
        self.assertEqual(capture.call_count,1)

    def test_banner_is_rejected_before_persistence_or_upload(self):
        for text in ('刚刚','微信(分身)','微信（分身）','微信'):
            with self.assertRaises(RuntimeError):
                assert_no_notification({'elements':[{'text':text,'center':[300,199]}]})

    def test_task_content_below_notification_area_is_accepted(self):
        assert_no_notification({'elements':[{'text':'今日任务','center':[540,175]},
                                           {'text':'微信','center':[500,1200]}]})

if __name__=='__main__':unittest.main()
