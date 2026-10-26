"""Narrow spaCy API TEST DOUBLE; deterministic tokens and synthetic dependency trees."""
from _readability_nlp import Doc, Span, Token, Parser, load
__version__ = '0.0-mock'

def blank(language):
    if language != 'en': raise NotImplementedError('Only the English API fixture is supported')
    return Parser()
