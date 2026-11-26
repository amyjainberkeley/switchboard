"""Text that came from outside this Mac's own records, made safe to print in a line.

An email or a name read from an accounts file, from another Mac's readings, or from a certificate a server showed was
chosen by whoever made it. Printed as it is, an escape sequence in it could clear the terminal, set its window title
or hide the rest of the line, and a double quote in it could end the quotes a sentence puts around it. So every such
value goes through said() before it is printed or logged.
"""

CAP = 120                   # characters: longer than any real email or certificate name


def said(text, cap=CAP):
    """`text` as one plain line: no control or format character (escape, bell, line break, tab, bidi mark, zero-width
    character: each is left out), a double quote turned into a single one so it cannot close a quote around it, spaces
    at either end taken off, and at most `cap` characters (the end cut and marked with ...). Not text: ''."""
    if not isinstance(text, str):
        return ''
    text = ''.join("'" if c == '"' else c for c in text if c.isprintable()).strip()
    return text if len(text) <= cap else text[:max(0, cap - 3)].rstrip() + '...'
