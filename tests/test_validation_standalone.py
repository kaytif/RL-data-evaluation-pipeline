"""Also runnable without third-party dependencies via unittest."""
import unittest
from pipeline.validation import grade, reference, correct_output, execute

class GraderTests(unittest.TestCase):
    def test_independent_expected_rows(self):
        self.assertEqual(reference(), [dict(id='t1', status='settled', amount='12.50'),
                                       dict(id='t3', status='settled', amount='7.25')])

    def test_false_positive_and_revision(self):
        self.assertTrue(grade(1, ''))
        self.assertFalse(grade(2, ''))
        self.assertTrue(grade(2, correct_output()))

    def test_extra_and_duplicate_rows_rejected(self):
        self.assertFalse(grade(2, correct_output() + 't1,settled,12.50\n'))
        self.assertFalse(grade(2, correct_output().replace('12.50', '12.50,extra')))

    def test_subprocess_reproduction(self):
        for version in (1, 2):
            evidence = execute(version)
            self.assertEqual(len(evidence['cases']), 7)
            matches = all(c['expected'] == c['actual'] for c in evidence['cases'])
            self.assertEqual(matches, version == 2)

if __name__ == '__main__':
    unittest.main()
