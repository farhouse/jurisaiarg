import unittest

from prepare_review import passages


class ReviewPassagesTest(unittest.TestCase):
    def test_notes_and_long_context_are_preserved(self):
        note = "(Artículo derogado por art. 2 de la Ley N° 27.700)"
        result = list(passages("Texto anterior. " + note + " Texto posterior."))
        self.assertEqual(result[0], (note, False))
        self.assertIn("Texto anterior.", result[1][0])
        self.assertIn("Texto posterior.", result[1][0])
        text = "abcdefghijklmnopqrstuvwxy"
        chunks = list(passages(text, width=10, overlap=2))
        self.assertTrue(all(divided for _, divided in chunks))
        self.assertEqual(chunks[0][0] + "".join(chunk[2:] for chunk, _ in chunks[1:]), text)
