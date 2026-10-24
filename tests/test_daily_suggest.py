import unittest
from daily_suggest import validate_answer_text

class AnswerTextTests(unittest.TestCase):
    def test_reason_answer_must_match_target_before_execution(self):
        with self.assertRaises(RuntimeError):
            validate_answer_text({'text':'an'},{'reason':'Answer="a"; consonant sound'})
        with self.assertRaises(RuntimeError):
            validate_answer_text({'text':'a'},{'reason':'Choose a, target nearby'})

    def test_exact_answer_text_accepts_single_letter_and_punctuation(self):
        validate_answer_text({'text':'a'},{'reason':'Answer="a"; consonant sound'})
        validate_answer_text({'text':'an; /'},{'reason':'Answer="an; /"; English teacher, football'})

if __name__=='__main__': unittest.main()
