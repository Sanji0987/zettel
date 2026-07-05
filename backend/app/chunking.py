"""Boundary-aware note chunking for Cognee ingest.

Most notes are small and stay a SINGLE chunk. Only notes longer than
CHUNK_WORD_THRESHOLD words are split — on paragraph boundaries first, then, for
an over-long paragraph, on sentence boundaries. It NEVER splits mid-sentence.

The caller (cognee_client.add_note_chunks) adds each chunk as a separate /add
call, all grouped under one node_set tag "note_<id>", then cognifies once.
"""
import os
import re

CHUNK_WORD_THRESHOLD = int(os.environ.get("CHUNK_WORD_THRESHOLD", "800"))

# Sentence boundary: end punctuation followed by whitespace. Good enough for prose;
# it never cuts inside a sentence, which is the only hard requirement.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_PARA_SPLIT = re.compile(r"\n\s*\n")


def _words(s: str) -> int:
    return len(s.split())


def _sentences(paragraph: str) -> list[str]:
    return [p for p in (s.strip() for s in _SENT_SPLIT.split(paragraph.strip())) if p]


def split_note(text: str, threshold: int | None = None) -> list[str]:
    """Split note text into chunks no larger than `threshold` words where possible.

    - Empty/whitespace -> [] (nothing to ingest).
    - <= threshold words -> [text] (single chunk; the common case).
    - Otherwise pack whole paragraphs up to the threshold; a single paragraph that
      alone exceeds it is packed by sentences. Sentences are never broken.
    """
    text = (text or "").strip()
    if not text:
        return []
    threshold = CHUNK_WORD_THRESHOLD if threshold is None else threshold
    if threshold <= 0 or _words(text) <= threshold:
        return [text]

    paragraphs = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    cur_words = 0

    def flush() -> None:
        nonlocal cur, cur_words
        if cur:
            chunks.append("\n\n".join(cur))
            cur = []
            cur_words = 0

    for para in paragraphs:
        pw = _words(para)
        if pw > threshold:
            # Paragraph alone is too big: emit whatever's buffered, then sentence-pack it.
            flush()
            sent_buf: list[str] = []
            sw = 0
            for sent in _sentences(para):
                s_words = _words(sent)
                if sent_buf and sw + s_words > threshold:
                    chunks.append(" ".join(sent_buf))
                    sent_buf = []
                    sw = 0
                sent_buf.append(sent)
                sw += s_words
            if sent_buf:
                chunks.append(" ".join(sent_buf))
        elif cur and cur_words + pw > threshold:
            flush()
            cur = [para]
            cur_words = pw
        else:
            cur.append(para)
            cur_words += pw

    flush()
    return chunks
