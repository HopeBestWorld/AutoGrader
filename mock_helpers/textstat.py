"""Readability-formula API TEST DOUBLE; heuristic syllables and fixture vocabulary."""
from _readability_nlp import Doc
import re

def _syllables(word):
    word=re.sub('[^a-z]','',word.lower())
    count=len(re.findall('[aeiouy]+',word))
    return max(1,count-(word.endswith('e') and not word.endswith('le') and count>1)) if word else 0

def _counts(text):
    doc = Doc(text); words = [t for t in doc if any(c.isalpha() for c in t.text)]
    return words, max(len(list(doc.sents)), 1), sum(_syllables(t.text) for t in words)
def flesch_kincaid_grade(text):
    words, sentences, syllables = _counts(text)
    return .39 * len(words) / sentences + 11.8 * syllables / max(len(words), 1) - 15.59
def flesch_reading_ease(text):
    words, sentences, syllables = _counts(text)
    return 206.835 - 1.015 * len(words) / sentences - 84.6 * syllables / max(len(words), 1)
def gunning_fog(text):
    words, sentences, _ = _counts(text)
    return .4 * (len(words) / sentences + 100 * sum(_syllables(t.text) >= 3 for t in words) / max(len(words), 1))
def dale_chall_readability_score(text):
    # Fixture-only vocabulary criterion; not the Dale–Chall word list.
    words, sentences, _ = _counts(text)
    unfamiliar = 100 * sum(len(t.text) > 7 for t in words) / max(len(words), 1)
    return .1579 * unfamiliar + .0496 * len(words) / sentences + (3.6365 if unfamiliar > 5 else 0)
