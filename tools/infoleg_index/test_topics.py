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
        self.assertEqual([m['label'] for m in result['matters']], [*list(MATTERS)[:2], 'Administrativo'])
        self.assertEqual(result['uncertain'], [list(MATTERS)[0], 'Administrativo'])
        self.assertEqual(result['concepts'], ['Expropiación', 'Indemnización'])
        self.assertEqual(result['decision'], 'revision')

    def test_overbroad_high_probability_output_is_never_automatic(self):
        class Router:
            def predict(self, text, questions, **kwargs):
                assert all('Private contracts' in q['criteria']['si'] for k,q in questions.items() if k=='m0')
                return {'answers':{key:{'choice':'si','probabilities':{'si':.99}} for key in questions}, 'usage':{'truncated':False}}
        result=classify_topics(Router(),'Texto de prueba.')
        self.assertEqual(result['decision'],'revision')
        self.assertIn('materias_demasiado_amplias',result['review_reasons'])
        self.assertEqual(result['matters'],[])
        self.assertEqual(len(result['model_matters']),len(MATTERS))
