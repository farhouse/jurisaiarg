"""E5 local compartido por indexación y consultas; sin servicios externos de inferencia."""
from functools import lru_cache
import os

MODEL = 'intfloat/multilingual-e5-small'
REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
# Cambiar también esta versión si cambia la segmentación/pooling/prefijos.
VERSION = REVISION + ':windows-v1'


@lru_cache(maxsize=1)
def runtime():
    import torch
    from transformers import AutoModel, AutoTokenizer
    torch.set_num_threads(max(1, int(os.getenv('ML_THREADS', '2'))))
    tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=REVISION)
    model = AutoModel.from_pretrained(MODEL, revision=REVISION).eval()
    return tokenizer, model


def fragments(text):
    """Ventanas exactas del original; 400 tokens + prefijo caben en 512."""
    tokenizer, _ = runtime()
    # Aquí solo medimos el original para dividirlo; no se envía completo al modelo.
    # encode() conserva el control de longitud antes de cualquier inferencia.
    offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True,
                        truncation=False, verbose=False)['offset_mapping']
    for start in range(0, len(offsets), 350):
        end = min(start + 400, len(offsets))
        yield text[offsets[start][0]:offsets[end - 1][1]]
        if end == len(offsets):
            break


def encode(texts, query=False):
    import torch
    import torch.nn.functional as functional
    tokenizer, model = runtime()
    batch = tokenizer([('query: ' if query else 'passage: ') + text for text in texts],
                      padding=True, truncation=False, return_tensors='pt')
    if batch['input_ids'].shape[1] > 512:
        raise ValueError('El texto excede 512 tokens; dividilo antes de vectorizar.')
    with torch.inference_mode():
        hidden = model(**batch).last_hidden_state
        mask = batch['attention_mask'].bool()
        pooled = hidden.masked_fill(~mask[..., None], 0).sum(dim=1) / mask.sum(dim=1)[..., None]
        return functional.normalize(pooled, p=2, dim=1).tolist()
