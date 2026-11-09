"""Independent visible-text and article coverage checks; no legal interpretation."""
import hashlib
import json
import re
import unicodedata
from html.parser import HTMLParser

POLICY = 'document-integrity-v2'
ARTICLE = re.compile(r'(?im)^\s*(?:art[ií]culo|art\.)\s*(\d+(?:\s*\.?\s*(?:bis|ter|qu[aá]ter))?)\s*[º°o]?\s*[-—–.]?')
HISTORY = re.compile(r'(?im)^\s*Antecedentes\s+Normativos\s*$')


def canonical(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', text or '').replace('\u00ad', '')).strip()


def digest(text):
    return hashlib.sha256(canonical(text).encode()).hexdigest()


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], []

    def handle_starttag(self, tag, attrs):
        if tag in ('head', 'script', 'style', 'template'):
            self.skip.append(tag)
        if not self.skip and tag in ('p', 'div', 'br', 'h1', 'h2', 'h3', 'li', 'tr', 'section', 'header', 'footer'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.skip and tag == self.skip[-1]:
            self.skip.pop()
        if not self.skip and tag in ('p', 'div', 'li', 'tr', 'section', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)
            self.parts.append(' ')


def visible_text(html):
    reader = VisibleText()
    reader.feed(html or '')
    return ''.join(reader.parts)


def article_key(value):
    return re.sub(r'[\s.º°]', '', value.lower())


def check_document(html, extracted, articles, version='original'):
    result = {'policy': POLICY, 'version': version, 'status': 'no_comprobable', 'issues': [], 'differences': []}
    if not html or html.lstrip().startswith('%PDF'):
        return {**result, 'issues': ['fuente_no_disponible']}
    source = visible_text(html)
    source_text, extracted_text = canonical(source), canonical(extracted)
    result.update(source_hash=digest(source), extracted_hash=digest(extracted))
    result['key'] = 'document:' + version + ':' + digest(source) + ':' + digest(extracted) + ':' + digest(json.dumps(articles,sort_keys=True,ensure_ascii=False))
    if not source_text or not extracted_text:
        return {**result, 'issues': ['texto_no_disponible']}
    if source_text != extracted_text:
        result['issues'].append('contenido_diferente')
        # Keep exact excerpts rather than asking someone to reread the whole source.
        from difflib import SequenceMatcher
        for tag, a, b, c, d in SequenceMatcher(None, source_text, extracted_text, autojunk=True).get_opcodes():
            if tag != 'equal':
                result['differences'].append({'source': source_text[max(0,a-60):min(len(source_text),b+60)][:600],
                                              'extracted': extracted_text[max(0,c-60):min(len(extracted_text),d+60)][:600]})
                if len(result['differences']) == 10:
                    break
    operative = HISTORY.split(source, maxsplit=1)[0]
    source_labels = [article_key(m.group(1)) for m in ARTICLE.finditer(operative)]
    labels = [article_key(str(a['label'])) for a in articles]
    if source_labels != labels:
        result['issues'].append('articulos_omitidos_duplicados_o_desordenados')
    source_matches=list(ARTICLE.finditer(operative))
    if source_labels==labels:
        for index,article in enumerate(articles):
            end=source_matches[index+1].start() if index+1<len(source_matches) else len(operative)
            expected=canonical(operative[source_matches[index].start():end])
            actual=canonical(article.get('text',''))
            if expected!=actual:
                result['issues'].append('contenido_del_articulo_diferente')
                result['differences'].append({'article':str(article['label']),'source':expected[:600],'extracted':actual[:600]})
    result['issues'] = list(dict.fromkeys(result['issues']))
    result['status'] = 'diferencias' if result['issues'] else 'coincide'
    result['article_count'] = len(articles)
    return result
