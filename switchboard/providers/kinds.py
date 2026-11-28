"""The kind of job a task is, worked out from the task's own words by fixed rules: rules, not a model.

kind_of(task) reads the task's first sentence (at most its first CUT characters), in lower case, and looks for the
words and phrases of TABLE in it, whole words only. A lookup ('find') is the answer only when no word of any other kind
is in the sentence: a task with words of two kinds takes the heavier one ("find and fix the bug" is a fix). Among the
other kinds the earliest match in the sentence decides; at the same place the longer phrase wins; then the order of
KINDS. When nothing matches, the kind is 'other'. The same words always give the
same answer. Nothing here reads a file, a clock or the network, and nothing here keeps or sends the task: the command
quotes back only the one word or phrase that decided.
"""
import re
from typing import Mapping, Optional, Sequence, Tuple

# The kinds, in the order that breaks a tie. What each is for: build, make or change code; fix, find and mend a
# fault; review, read work and judge it; plan, decide how before doing; find, look something up or explain what is
# there; write, prose (a doc, a message, a summary); other, none of these.
KINDS = ('build', 'fix', 'review', 'plan', 'find', 'write', 'other')
CUT = 300                   # characters of the first sentence that are read
LIGHT = 'find'              # the answer only when no word of another kind is in the sentence

# What is written as prose. "write a" and "write the" alone are a build job ("write the parser"); with one of these
# next they are a piece of writing ("write the release notes"), by the rule that the longer phrase wins at its place.
PROSE = ('readme', 'changelog', 'release notes', 'summary', 'memo', 'email', 'blog post', 'announcement', 'docs')

# The words and phrases of each kind, in lower case, one space between words, each once in the whole table. A longer
# phrase that starts with a shorter one of another kind wins at that place ("what is wrong" over "what is").
TABLE = {
    'build': ('add', 'implement', 'build', 'create', 'make', 'refactor', 'migrate', 'rename', 'port', 'update',
              'write a', 'write the', 'change', 'convert', 'replace', 'remove', 'delete', 'move', 'extend', 'upgrade',
              'set up', 'wire up', 'integrate', 'redesign'),
    'fix': ('fix', 'debug', 'broken', 'failing', 'fails', 'failed', 'crash', 'crashes', 'crashing', 'bug', 'bugs',
            'error', 'errors', 'why does', 'why is', 'why are', 'why do', 'repair', 'mend', 'resolve', 'regression',
            'not working', 'what is wrong'),
    'review': ('review', 'audit', 'critique', 'assess', 'evaluate', 'second opinion', 'look over', 'check over',
               'proofread', 'sanity check'),
    'plan': ('plan', 'design', 'propose', 'outline', 'how should', 'how would', 'strategy', 'architecture', 'roadmap',
             'spec', 'brainstorm', 'think through'),
    'find': ('find', 'where', 'which', 'list', 'explain', 'what does', 'look up', 'what is', 'what are', 'how does',
             'how do', 'locate', 'search', 'show me', 'tell me', 'count', 'who'),
    'write': ('draft', 'summarize', 'summarise', 'document', 'write up', 'translate') + PROSE + tuple(
        'write %s %s' % (article, noun) for noun in PROSE for article in ('a', 'an', 'the')),
}

# Where the first sentence ends: . ! or ? before a blank or the end, or a line end. A dot inside a word (foo.py)
# does not end it.
_END_RE = re.compile(r'[.!?](?=\s|\Z)|\n')
# A list mark the task may begin with ("1. ", "- "): not part of the sentence.
_MARK_RE = re.compile(r'\A\s*(?:\d{1,3}[.)]|[-*•])\s+')


def first_sentence(task) -> str:
    """The task's first sentence in lower case, cut to CUT characters ('' for no task)."""
    if not isinstance(task, str):
        task = '' if task is None else str(task)
    text = _MARK_RE.sub('', task.lstrip(), count=1)
    m = _END_RE.search(text)
    return (text[:m.start()] if m else text)[:CUT].lower()


def _pattern(phrase: str):
    words = [re.escape(w) for w in phrase.split()]
    return re.compile(r'(?<!\w)' + r'\s+'.join(words) + r'(?!\w)')


def _compiled(table: Mapping[str, Sequence[str]]):
    return [(kind, phrase, _pattern(phrase)) for kind in KINDS for phrase in table.get(kind, ())]


_TABLE = _compiled(TABLE)


def kind_of(task, table: Optional[Mapping[str, Sequence[str]]] = None) -> Tuple[str, Optional[str]]:
    """(kind, the word or phrase that decided it) for a task, or ('other', None) when nothing matched. table: a
    made-up table in TABLE's shape, for the tests; the module's own when not given."""
    sentence = first_sentence(task)
    if not sentence.strip():
        return 'other', None
    best = None
    for kind, phrase, rx in (_TABLE if table is None else _compiled(table)):
        m = rx.search(sentence)
        if m is None:
            continue
        rank = (kind == LIGHT, m.start(), -len(phrase.split()), -len(phrase), KINDS.index(kind))
        if best is None or rank < best[0]:
            best = (rank, kind, phrase)
    return (best[1], best[2]) if best else ('other', None)
