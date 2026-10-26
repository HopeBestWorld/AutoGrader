"""Reviewed readability API TEST DOUBLE; not the instructor's library.

Regex tokenization, heuristic syllables and synthetic dependency trees exercise
caller code only. CLEAR fixtures and model replies are synthetic, not research
results. Assertions in the caller remain active.
"""
from dataclasses import dataclass
from html import escape
from pathlib import Path
import re
import numpy as np
import pandas as pd
from _readability_nlp import Doc, Span, Parser, load as load_parser

MODEL_APIS_ENABLED = False
CLEAR_URL = 'mock://readability-clear'
TEAL = '#138d90'


def is_word(token): return any(c.isalpha() for c in token.text)
def sentences(doc): return [s for s in doc.sents if any(is_word(t) for t in s)]
def sentence_words(sentence): return [t for t in sentence if is_word(t)]
def count_syllables_heuristic(word):
    word = re.sub('[^a-z]', '', str(word).lower())
    if not word: return 0
    count = len(re.findall('[aeiouy]+', word))
    if word.endswith('e') and not word.endswith(('le', 'ye')) and count > 1: count -= 1
    return max(1, count)
count_syllables = count_syllables_heuristic
count_syllables_cmu = count_syllables_heuristic

def dependency_distances(doc):
    result = {}
    for sentence in sentences(doc):
        positions = {t.i: i for i, t in enumerate(sentence_words(sentence))}
        result.update({t.i: abs(positions[t.i] - positions[t.head.i]) for t in sentence
                       if t.i in positions and t.head.i in positions and t.head.i != t.i})
    return result

def depth_values(doc): return {t.i: sum(1 for _ in t.ancestors) for t in doc if is_word(t)}
def clause_heads(sentence): return [t for t in sentence if t.dep_ in ('advcl', 'ccomp', 'xcomp', 'acl', 'relcl')]
def structure_features(doc):
    sents = sentences(doc); words = [t for t in doc if is_word(t)]
    nouns = [t for t in words if t.pos_ in ('NOUN', 'PROPN')]
    return {'clauses_per_sentence': sum(len(clause_heads(s)) for s in sents) / max(len(sents), 1),
            'modifiers_per_noun': sum(t.dep_ in ('amod', 'nmod', 'compound') for t in words) / max(len(nouns), 1)}


@dataclass
class Measure:
    key: str
    name: str
    token_values: object
    color: str = TEAL
    vmax: float = 10
    sentence_label: str = 'mean'
    tooltip: object = None
    description: str = ''
    def __post_init__(self):
        if not isinstance(self.key, str) or not callable(self.token_values): raise TypeError('Measure requires a string key and callable token_values')
    def view_html(self, doc): return token_highlight_html(doc, self.token_values(doc), color=self.color)
    def doc_value(self, doc):
        values=self.token_values(doc)
        if not isinstance(values,dict):raise TypeError('Measure token_values must return a token-index dictionary')
        return float(np.mean(list(values.values()))) if values else 0.0


SYLLABLES = Measure('syllables', 'Syllables / Flesch–Kincaid', lambda d: {t.i: count_syllables(t.text) for t in d if is_word(t)})
DEPENDENCY_DISTANCE = Measure('dep_distance', 'Dependency distance', dependency_distances)
DEPTH = Measure('depth', 'Tree depth', depth_values)
BUILTIN_MEASURES = [SYLLABLES, DEPENDENCY_DISTANCE, DEPTH]
FEATURE_NAMES = {'syllables_per_word': 'Syllables per word', 'words_per_sentence': 'Words per sentence',
                 'fk_grade': 'Flesch–Kincaid grade', 'mean_dep_distance': 'Mean dependency distance',
                 'max_dep_distance': 'Maximum dependency distance', 'mean_max_dep_distance': 'Mean maximum dependency distance',
                 'mean_depth': 'Mean tree depth', 'max_depth': 'Maximum tree depth', 'mean_max_depth': 'Mean maximum tree depth',
                 'clauses_per_sentence': 'Subordinate clauses per sentence', 'modifiers_per_noun': 'Noun modifiers per noun'}


def document_features(doc, measures=BUILTIN_MEASURES):
    if isinstance(doc, str): raise TypeError('Pass a parsed document to document_features')
    words = [t for t in doc if is_word(t)]; sents = sentences(doc)
    features = structure_features(doc)
    # The base feature schema is always available; measures may add custom columns.
    by_key={m.key:m for m in BUILTIN_MEASURES}
    by_key.update({m.key:m for m in measures})
    for measure in by_key.values():
        values = measure.token_values(doc)
        if not isinstance(values, dict): raise TypeError('Measure token_values must return a token-index dictionary')
        numbers = list(values.values())
        mean = float(np.mean(numbers)) if numbers else 0.0
        if measure.key == 'syllables':
            wps = len(words) / max(len(sents), 1)
            features.update(syllables_per_word=mean, words_per_sentence=wps, fk_grade=0.39 * wps + 11.8 * mean - 15.59)
        elif measure.key in ('depth', 'dep_distance'):
            key = measure.key
            maxima = [max((values[t.i] for t in sentence if t.i in values), default=0) for sentence in sents]
            features.update({f'mean_{key}': mean, f'max_{key}': max(numbers, default=0), f'mean_max_{key}': float(np.mean(maxima)) if maxima else 0.0})
        else: features[measure.key] = mean
    return features


def feature_table(docs, measures=BUILTIN_MEASURES, index=None):
    if isinstance(docs, dict):
        if index is not None: raise ValueError('Choose either dictionary labels or an explicit index')
        index, docs = list(docs), list(docs.values())
    return pd.DataFrame([document_features(d, measures) for d in docs], index=index)

def feature_labels(measures=BUILTIN_MEASURES):
    labels = dict(FEATURE_NAMES)
    labels.update({m.key: m.name for m in measures if m.key not in ('syllables', 'depth', 'dep_distance')})
    return labels

def sentence_table(doc, measures=BUILTIN_MEASURES):
    return pd.DataFrame([{**document_features(s, measures), 'words': len(sentence_words(s)), 'sentence': s.text,'sentence_number':i+1,
                          'clauses':len(clause_heads(s)), 'noun_modifiers':sum(t.dep_ in ('amod','nmod','compound') for t in s)} for i,s in enumerate(sentences(doc))])

def decode_bytes(data):
    if not isinstance(data, bytes): raise TypeError('Expected bytes')
    return data.decode('utf-8-sig')

def text_to_paragraphs(text):
    if not isinstance(text, str): raise TypeError('Expected a text string')
    if '<html' in text.lower() or '<body' in text.lower():
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(text,'html.parser')
        blocks=soup.find_all(['p','h1','h2','h3','pre'])
        text='\n\n'.join(block.get_text() for block in blocks) if blocks else soup.get_text()
    return [' '.join(p.split()) for p in re.split(r'\n\s*\n', text.replace('\r\n', '\n')) if p.strip()]
split_paragraphs = text_to_paragraphs

def clean_paragraphs(text, min_words=5, start_at=None, end_at=None, max_words=None):
    text='\n\n'.join(' '.join(p.split()) for p in text_to_paragraphs(text))
    if start_at:
        match=re.search(re.escape(start_at),text) or re.search(re.escape(start_at),text,re.I)
        if match is None:raise ValueError('Start marker not found in supplied text: '+start_at)
        text = text[match.start():]
    if end_at:
        match=re.search(re.escape(end_at),text) or re.search(re.escape(end_at),text,re.I)
        if match is None:raise ValueError('End marker not found in supplied text: '+end_at)
        text = text[:match.start()]
    paragraphs=[' '.join(p.split()) for p in text_to_paragraphs(text) if len(p.split()) >= min_words]
    if max_words is None:return paragraphs
    if not isinstance(max_words,int) or max_words<=0:raise ValueError('max_words must be positive')
    selected=[];remaining=max_words
    for paragraph in paragraphs:
        words=paragraph.split()[:remaining]
        if words:selected.append(' '.join(words));remaining-=len(words)
        if not remaining:break
    return selected

def parse_paragraphs(nlp, paragraphs, batch_size=64): return list(nlp.pipe(paragraphs,batch_size=batch_size))
def parse_document(nlp, text): return nlp(text)
def join_docs(docs): return Doc('\n\n'.join(d.text for d in docs))
def chunk_paragraphs(paragraphs, words_per_chunk=300):
    if not isinstance(words_per_chunk, int) or words_per_chunk <= 0: raise ValueError('words_per_chunk must be a positive integer')
    chunks, current, count = [], [], 0
    for paragraph in paragraphs:
        # Whole paragraphs stay intact so token counts and chapter membership align.
        current.append(paragraph.text); count += sum(is_word(t) for t in paragraph)
        if count >= words_per_chunk:
            chunks.append(Doc(' '.join(current))); current, count = [], 0
    if current: chunks.append(Doc(' '.join(current)))
    return chunks


def token_highlight_html(doc, values, color=TEAL, vmax=None, tooltip=None):
    if not isinstance(values, dict): raise TypeError('Highlight values must be keyed by token index')
    return '<div><strong>MOCK NLP visualization — synthetic parse</strong><p>' + ' '.join(
        '<span title="' + escape(str(values.get(t.i, '')), quote=True) + '">' + escape(t.text) + '</span>' for t in doc) + '</p></div>'

def clause_view_html(doc): return token_highlight_html(doc, {t.i: t.dep_ for t in doc})
def arc_diagram_html(sentence, show_labels=True, vmax=None): return token_highlight_html(sentence, {t.i: f'{t.head.i}: {t.dep_}' for t in sentence},vmax=vmax)

def trajectory_chart(table, key, label, window=5, reference=None):
    import altair as alt
    data = table[[key]].copy().reset_index(drop=True)
    data['passage'] = np.arange(len(data))
    chart=alt.Chart(data).mark_line().encode(x='passage:Q', y=alt.Y(key + ':Q', title=label))
    if reference is not None:
        if isinstance(reference,pd.DataFrame):value=float(reference[key].mean())
        elif isinstance(reference,pd.Series):value=float(reference[key]) if key in reference.index else float(reference.mean())
        elif isinstance(reference,dict):value=float(reference[key])
        else:value=float(np.mean(reference))
        chart=chart+alt.Chart(pd.DataFrame({'reference':[value]})).mark_rule().encode(y='reference:Q')
    return chart

def percentile_in(reference, value):
    reference = np.asarray(reference, dtype=float)
    if not len(reference): raise ValueError('Empty reference population')
    return float(np.mean(reference <= value) * 100)

def percentile_table(values, reference, measures=None):
    if isinstance(values, pd.Series): values = values.to_frame().T
    keys=list(values.columns) if measures is None else [k for k in feature_labels(measures) if k in values]
    return pd.DataFrame({key: [percentile_in(reference[key], value) for value in values[key]] for key in keys}, index=values.index)

def percentile_chart(table, labels=None):
    import altair as alt
    data = table.copy(); data.index.name = 'document'
    return alt.Chart(data.reset_index().melt(id_vars='document', var_name='measure', value_name='percentile')).mark_bar().encode(x='measure:N', y='percentile:Q', color='document:N')

def load_clear(path=None):
    if path is None or str(path) == CLEAR_URL:
        path = Path(__file__).with_name('_mock_readability_clear.csv')
    table = pd.read_csv(path)
    if 'excerpt' not in table or 'difficulty' not in table: raise ValueError('CLEAR fixture requires excerpt and difficulty columns')
    features = feature_table(Parser().pipe(table['excerpt']))
    for key in features:
        if key not in table: table[key] = features[key].to_numpy()
    return table

def difficulty_model(features, targets):
    keys = [k for k in FEATURE_NAMES if k in features]
    if not keys and 'excerpt' in features:
        features=feature_table(Parser().pipe(features['excerpt']),index=features.index)
        keys=list(features.columns)
    if not keys: raise ValueError('Difficulty model needs readability feature columns')
    matrix = np.column_stack([np.ones(len(features)), features[keys].to_numpy(dtype=float)])
    coefficients = np.linalg.lstsq(matrix, np.asarray(targets, dtype=float), rcond=None)[0]
    def predict(table):
        if any(k not in table for k in keys) and 'excerpt' in table:
            table=feature_table(Parser().pipe(table['excerpt']),index=table.index)
        design = np.column_stack([np.ones(len(table)), table[keys].to_numpy(dtype=float)])
        return pd.Series(design @ coefficients, index=table.index)
    return predict


@dataclass(frozen=True)
class FixtureLanguageModel:
    name: str

def load_language_model(name='gpt2'):
    if not MODEL_APIS_ENABLED: raise NotImplementedError('Model-API fixtures were not enabled')
    if name != 'gpt2': raise NotImplementedError('Only the GPT-2 API fixture is defined')
    return FixtureLanguageModel(name), FixtureLanguageModel(name)

def word_surprisals(doc, tokenizer, model):
    if not isinstance(model, FixtureLanguageModel): raise TypeError('Expected the explicit model fixture')
    return {t.i: float(1 + len(t.text) % 9) for t in doc if is_word(t)}
def piece_surprisals(doc, tokenizer, model):
    raise NotImplementedError('Subword tokenization is outside the model fixture contract')
def make_surprisal_measure(tokenizer, model, name='GPT-2', model_name=None):
    if model_name is not None:name=model_name
    return Measure('mean_surprisal', name + ' (synthetic API fixture)', lambda d: word_surprisals(d, tokenizer, model))

def fetch_url(url, cache_dir='downloads'):
    if not str(url).startswith(('http://','https://')):
        local=Path(url)
        if not local.is_file():raise FileNotFoundError(local)
        return local.read_text(encoding='utf-8-sig')
    # Network retrieval is not replaced with invented text by this helper.
    from urllib.request import urlopen
    from urllib.parse import urlparse
    target = Path(cache_dir) / (Path(urlparse(url).path).name or 'download.txt')
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(url, timeout=15) as response: target.write_bytes(response.read())
    return target.read_text(encoding='utf-8-sig')
