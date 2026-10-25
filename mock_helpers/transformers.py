"""Explicit text-generation API TEST DOUBLE. No real model, inference or network."""
import hashlib

class MockModelUnsupported(NotImplementedError):
    pass

class _Logging:
    def set_verbosity_error(self):
        pass
logging=_Logging()


def pipeline(task, model=None, **kwargs):
    if task!='text-generation':
        raise MockModelUnsupported('Mock model API only supports text-generation chat requests')
    if model is not None and not isinstance(model,str):
        raise TypeError('model must be a string identifier')
    if kwargs:
        raise MockModelUnsupported('Mock model API does not support these pipeline options: '+', '.join(kwargs))
    def generate(messages,max_new_tokens=5,do_sample=False,**options):
        if options:
            raise MockModelUnsupported('Mock model API does not support these generation options: '+', '.join(options))
        if not isinstance(messages,list) or not messages or any(not isinstance(m,dict) or not isinstance(m.get('content'),str) or m.get('role') not in ('system','user','assistant') for m in messages):
            raise TypeError('Expected chat messages with string role/content fields')
        if not isinstance(max_new_tokens,int) or max_new_tokens<=0 or not isinstance(do_sample,bool):
            raise TypeError('Invalid generation argument types')
        # Hash-based fixture labels are intentionally NOT sentiment predictions.
        content=messages[-1]['content']
        answer=('positive','negative','neutral')[hashlib.sha256(content.encode()).digest()[0]%3]
        return [{'generated_text':[dict(m) for m in messages]+[{'role':'assistant','content':answer}]}]
    return generate
