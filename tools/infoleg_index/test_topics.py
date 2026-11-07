import unittest
from topics import classify_topics, MATTERS


class TopicsTest(unittest.TestCase):
    def test_multilabel_uncertainty_and_literal_concepts(self):
        class Router:
            def predict(self, text, questions, **kwargs):
                return {'answers': {key: {'choice': 'si' if i < 2 else 'no',
                    'probabilities': {'si': .6 if i == 0 else .9, 'no': .95}} for i, key in enumerate(questions)},
                    'usage': {'truncated': False}}
        result = classify_topics(Router(), 'La expropiación requiere indemnización.')
        self.assertEqual([m['label'] for m in result['matters']], list(MATTERS)[:2])
        self.assertEqual(result['uncertain'], [list(MATTERS)[0]])
        self.assertEqual(result['concepts'], ['Expropiación', 'Indemnización'])
        self.assertEqual(result['decision'], 'revision')
