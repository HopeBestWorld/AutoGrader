"""Word-frequency API TEST DOUBLE; deterministic fixture values, not corpus frequencies."""
import hashlib

def zipf_frequency(word, lang, wordlist='best', minimum=0.0):
    if lang != 'en' or not isinstance(word, str): raise ValueError('Expected an English word string')
    return max(minimum, int.from_bytes(hashlib.sha256(word.lower().encode()).digest()[:2], 'big') / 65535 * 6)
