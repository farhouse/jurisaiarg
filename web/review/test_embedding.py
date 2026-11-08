import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from . import embedding


class EmbeddingWindowTest(unittest.TestCase):
    def test_long_text_is_split_without_warning_or_loss_and_oversize_is_rejected(self):
        import torch
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers import PreTrainedTokenizerFast

        # Tokenizador real, vocabulario mínimo: sin descargar pesos para esta prueba.
        backend = Tokenizer(models.WordLevel(
            {'[UNK]': 0, 'inicio': 1, 'ley': 2, 'final': 3, 'passage': 4, ':': 5},
            unk_token='[UNK]'))
        backend.pre_tokenizer = pre_tokenizers.Whitespace()
        tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]',
                                           pad_token='[UNK]', model_max_length=512)
        model = Mock(side_effect=lambda **batch: SimpleNamespace(
            last_hidden_state=torch.ones((*batch['input_ids'].shape, 384))))
        words = ['inicio'] + ['ley'] * 773 + ['final']
        text = ' '.join(words)
        with patch.object(embedding, 'runtime', return_value=(tokenizer, model)):
            with self.assertNoLogs('transformers.tokenization_utils_base', level='WARNING'):
                parts = list(embedding.fragments(text))
                model.assert_not_called()
                rebuilt = parts[0].split()
                for part in parts[1:]:
                    rebuilt.extend(part.split()[50:])
                self.assertEqual(rebuilt, words)
                self.assertGreater(len(parts), 1)
                for part in parts:
                    vector = embedding.encode([part])[0]
                    self.assertEqual(len(vector), 384)
                self.assertTrue(all(call.kwargs['input_ids'].shape[1] <= 512
                                    for call in model.call_args_list))
            model.reset_mock()
            with self.assertLogs('transformers.tokenization_utils_base', level='WARNING'):
                with self.assertRaisesRegex(ValueError, 'excede 512 tokens'):
                    embedding.encode([text])
            model.assert_not_called()
