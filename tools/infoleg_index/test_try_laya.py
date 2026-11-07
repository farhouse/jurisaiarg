import unittest
from unittest.mock import Mock

from try_laya import classify, review_decision


class LayaExperimentTest(unittest.TestCase):
    def test_combined_policy_requires_agreement_and_complete_evidence(self):
        note = "(Artículo derogado por art. 2 de la Ley N° 27.700)"
        self.assertEqual(review_decision(note, "deroga", 0.75, True)["decision"], "aceptacion_automatica")
        cases = [
            (note, "deroga", 0.749, True, "probabilidad_baja"),
            (note, "sustituye", 0.99, True, "desacuerdo_parser_laya"),
            (note, "ninguno", 0.99, True, "desacuerdo_parser_laya"),
            (note, "deroga", 0.99, False, "texto_truncado_o_no_verificado"),
            (note, "deroga", float("nan"), True, "probabilidad_invalida"),
            (note.replace("Artículo", "Inciso"), "deroga", 0.99, True, "alcance_parcial_sin_identificar"),
            (note + " (Artículo derogado por art. 3 de la Ley N° 27.701)", "deroga", 0.99, True, "multiples_cambios"),
            ("El proyecto propone derogar el artículo 5, pero todavía no fue aprobado.", "deroga", 0.9919, True, "sin_extraccion_estructurada"),
        ]
        for text, label, probability, complete, reason in cases:
            with self.subTest(reason=reason):
                result = review_decision(text, label, probability, complete)
                self.assertTrue(result["requires_review"])
                self.assertIn(reason, result["review_reasons"])
        self.assertEqual(review_decision("Ver Ley 20.744.", "ninguno", 0.80, True)["decision"], "sin_cambio_detectado")
        for threshold in (-0.1, 1.1, float("nan")):
            with self.assertRaises(ValueError):
                review_decision(note, "deroga", 0.99, True, threshold)

    def test_prediction_is_observational_and_truncated_text_is_not_scored(self):
        case = {"text": "(Artículo derogado por art. 2 de la Ley N° 27.700)", "expected": "deroga"}
        raw = {"answers": {"change_type": {"choice": "deroga", "probabilities": {"deroga": 0.91}}},
               "usage": {"truncated": False}}
        router = Mock()
        router.predict.return_value = raw
        result = classify(router, case)
        self.assertTrue(result["expected_match"])
        self.assertTrue(result["parser_agreement"])
        self.assertEqual(result["status"], "experimental")
        self.assertEqual(router.predict.call_args.kwargs["model"], "multilingual")
        multiple = {"text": case["text"] + " (Artículo incorporado por art. 3 de la Ley N° 27.700)"}
        self.assertFalse(classify(router, multiple)["parser_agreement"])
        for usage in ({"truncated": True}, {}):
            raw["usage"] = usage
            result = classify(router, case)
            self.assertIsNone(result["expected_match"])
            self.assertIsNone(result["parser_agreement"])
        raw["answers"]["change_type"]["probabilities"]["deroga"] = float("nan")
        with self.assertRaises(ValueError):
            classify(router, case)


if __name__ == "__main__":
    unittest.main()
