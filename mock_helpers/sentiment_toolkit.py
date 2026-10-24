"""Simplified sentiment helper TEST DOUBLE; not the original instructor library."""
import re
from pathlib import Path
import pandas as pd
import numpy as np

def tokenize(text):
    return re.findall(r"\w[\w\-']*\w|\w",text.lower())
def load_segments(path,words_per_segment=150):
    words=Path(path).read_text(encoding='utf-8-sig').split()
    texts=[' '.join(words[i:i+words_per_segment]) for i in range(0,len(words),words_per_segment)]
    return pd.DataFrame({'text':texts,'words':[len(t.split()) for t in texts],'position':np.arange(len(texts))/max(len(texts),1)})
def load_lexicon(path):
    result={}
    for line in Path(path).read_text().splitlines():
        weight,word=line.split(',');result[word]=float(weight)
    return result
def load_emolex(path,emotion):
    result={}
    for line in Path(path).read_text().splitlines():
        word,label,flag=line.split('\t')
        if label==emotion and flag=='1':result[word]=1.0
    return result
def score_lexicon(text,lexicon,normalize=True):
    words=tokenize(text);score=sum(lexicon.get(w,0) for w in words)
    return score/max(len(words),1) if normalize else score
def add_score(segments,name,score_fn,progress=False):
    return segments.assign(**{name:[score_fn(t) for t in segments.text]})
def sort_by_score(segments,column,n=5,ascending=False):
    return segments.sort_values(column,ascending=ascending).head(n)
def show_passages(segments,columns=None,**kwargs):
    # Display-only return: does not replace any calculation or suppress errors.
    return '\n\n'.join(str(t) for t in segments.text)
def plot_timeseries(segments,columns,window=10,standardize=True):
    import altair as alt
    if isinstance(columns,str):columns=[columns]
    data=segments[['position']+list(columns)].copy()
    for column in columns:
        values=data[column]
        if standardize:values=(values-values.mean())/values.std()
        data[column]=values.rolling(window,center=True,min_periods=1).mean()
    return alt.Chart(data.melt(id_vars='position',var_name='score',value_name='value')).mark_line().encode(x='position:Q',y='value:Q',color='score:N')
def score_classifier(*args,**kwargs):
    raise NotImplementedError('Classifier behavior is not provided by the sentiment helper test double')
def score_llm(*args,**kwargs):
    raise NotImplementedError('Language-model behavior is not provided by the sentiment helper test double')
