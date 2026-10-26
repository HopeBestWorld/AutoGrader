"""Deterministic NLP API TEST DOUBLE. Synthetic trees/tags are not linguistic parses."""
import re
import operator


class Token:
    def __init__(self, doc, i, match):
        self.doc, self.i, self.idx, self.text = doc, i, match.start(), match.group()
    def __str__(self): return self.text
    def __len__(self): return len(self.text)
    @property
    def is_alpha(self): return self.text.isalpha()
    @property
    def is_space(self): return self.text.isspace()
    @property
    def is_punct(self): return not any(c.isalnum() for c in self.text) and not self.is_space
    @property
    def lower_(self): return self.text.lower()
    @property
    def whitespace_(self):
        end=self.doc._tokens[self.i+1].idx if self.i+1<len(self.doc._tokens) else len(self.doc.text)
        return self.doc.text[self.idx+len(self.text):end]
    @property
    def text_with_ws(self): return self.text+self.whitespace_
    @property
    def pos_(self):
        return 'PUNCT' if self.is_punct else ('NOUN', 'VERB', 'ADJ', 'ADV', 'PROPN')[self.i % 5]
    @property
    def dep_(self):
        return 'ROOT' if self.doc._heads[self.i] == self.i else ('nsubj', 'obj', 'amod', 'advcl', 'ccomp', 'det')[self.i % 6]
    @property
    def head(self): return self.doc[self.doc._heads[self.i]]
    @property
    def ancestors(self):
        current = self
        while current.head.i != current.i:
            current = current.head
            yield current
    @property
    def children(self): return iter(self.doc._children.get(self.i, []))
    @property
    def subtree(self):
        pending, found = [self], []
        while pending:
            current = pending.pop(); found.append(current); pending.extend(current.children)
        return iter(sorted(found, key=lambda t: t.i))
    @property
    def sent(self):
        start, end = self.doc._sent_bounds[self.i]
        return Span(self.doc, start, end)


class Span:
    def __init__(self, doc, start, end): self.doc, self.start, self.end = doc, start, end
    def __iter__(self): return iter(self.doc._tokens[self.start:self.end])
    def __len__(self): return self.end - self.start
    def __str__(self): return self.text
    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            if step != 1: raise ValueError('Stepped spans are unsupported by the NLP fixture')
            return Span(self.doc, self.start + start, self.start + stop)
        index=operator.index(key)
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError('Token index outside the span')
        return self.doc._tokens[self.start+index]
    @property
    def text(self):
        if not len(self): return ''
        return self.doc.text[self[0].idx:self[-1].idx + len(self[-1].text)]
    @property
    def sents(self):
        return (Span(self.doc, max(s, self.start), min(e, self.end)) for s, e in self.doc._sentences if e > self.start and s < self.end)
    @property
    def root(self): return next((t for t in self if t.head.i == t.i), self[0])
    def as_doc(self): return Doc(self.text)


class Doc(Span):
    def __init__(self, text):
        if not isinstance(text, str): raise TypeError('NLP fixture expects a string')
        self._text = text
        self._tokens = [Token(self, i, m) for i, m in enumerate(re.finditer(r"\w+(?:['’\-]\w+)*|[^\w\s]", text))]
        self.doc, self.start, self.end = self, 0, len(self._tokens)
        self._sentences = []
        start = 0
        for t in self._tokens:
            if t.text in '.!?':
                self._sentences.append((start, t.i + 1)); start = t.i + 1
        if start < self.end: self._sentences.append((start, self.end))
        self._heads, self._sent_bounds, self._children = {}, {}, {}
        for start, end in self._sentences:
            for i in range(start, end):
                # Fixture-only acyclic dependency structure; never a trained parse.
                head = start if i == start else max(start, i - (1 + i % 5))
                self._heads[i] = head; self._sent_bounds[i] = (start, end)
                if head != i: self._children.setdefault(head, []).append(self._tokens[i])
    @property
    def text(self): return self._text


class Parser:
    def __init__(self):
        self.max_length = 10_000_000; self.pipe_names = ['mock_parser']
        self.meta = {'lang':'en','name':'synthetic_mock','version':'0.0-mock'}
    def __call__(self, text): return text if isinstance(text, Doc) else Doc(text)
    def make_doc(self, text): return Doc(text)
    def pipe(self, texts, batch_size=64, n_process=1, disable=()):
        if n_process != 1: raise NotImplementedError('The NLP fixture supports a single process')
        return (self(text) for text in texts)
    def add_pipe(self, name):
        if name != 'sentencizer': raise NotImplementedError(f'Unsupported NLP fixture pipeline: {name}')
        self.pipe_names.append(name)
        return self


def load(model='en_core_web_sm', *, disable=(), exclude=()):
    if model != 'en_core_web_sm': raise NotImplementedError(f'No reviewed fixture for model {model}')
    return Parser()
