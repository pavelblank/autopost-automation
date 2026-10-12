# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""
AutoPost - textsan.py
Readable-text guard. Scraped topic titles arrive HTML-escaped from their
source (YouTube/Reddit/Trends XML all hand back &quot; / &#39; / &amp;),
and nothing downstream ever decoded them - so the raw entity leaked straight
into the published post and onto blog.your-domain.example, which renders `topic`
verbatim. The post body itself was fine; only the topic carried the damage.

sanitize()  - decode entities so a human reads a real character.
problems()  - deterministic "would a human be confused by this?" check that
              does NOT rely on the LLM auditor, because a small local model
              will happily wave &quot; straight through.
"""

import html
import re

# Entities that are always wrong in a human-facing caption. Deliberately
# excludes &amp; &lt; &gt; &nbsp; - those can be legitimate text, and
# unescaping them is handled by sanitize() anyway.
BAD = ("&quot;", "&#34;", "&#x27;", "&apos;", "&amp;", "&lt;", "&gt;",
       "&nbsp;", "&#39;", "&mdash;", "&ndash;", "&hellip;", "&rsquo;",
       "&lsquo;", "&ldquo;", "&rdquo;", "&copy;", "&reg;", "&trade;")

# Named refs that survived a double-escape (&amp;quot; -> still an entity).
_REF = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]{1,30}|#x?[0-9a-fA-F]{1,6});")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize(text, rounds=3):
    """Decode HTML entities until stable. Handles double-escaped junk like
    '&amp;quot;' which needs two passes, and never loops forever."""
    if not text:
        return text
    out = str(text)
    for _ in range(rounds):
        new = html.unescape(out)
        if new == out:
            break
        out = new
    return out


def problems(text):
    """Return a list of PLAIN-ENGLISH problem strings. Empty list = clean.
    Messages are written for a non-technical reader - this text ends up in
    the dashboard and in the Telegram alert."""
    if not text:
        return []
    bad = []
    raw = str(text)

    hits = [e for e in BAD if e in raw]
    if hits:
        shown = ", ".join(sorted(set(hits))[:4])
        bad.append(
            "Text shows raw HTML codes instead of real characters (%s). "
            "It should be a normal quote or apostrophe." % shown
        )
    elif _REF.search(raw):
        m = _REF.search(raw).group(0)
        bad.append(
            "Text contains a leftover web code (%s) that a reader would see "
            "as gibberish." % m
        )

    if _CTRL.search(raw):
        bad.append("Text contains invisible control characters that may break the post.")

    if "Ã¢" in raw or "Ã©" in raw or "Ã¨" in raw or "â€™" in raw:
        bad.append("Text has broken character encoding - letters may look scrambled.")

    # Mojibake / mojibake-adjacent: the UTF-8 bytes of an em dash or curly
    # quote read as three latin-1 chars, which is what a missed unescape or a
    # bad decode leaves behind.
    if re.search(r"[ÂÃ][\x80-\xbf-ÿ]", raw):
        bad.append("Text has broken character encoding - letters may look scrambled.")

    return bad
