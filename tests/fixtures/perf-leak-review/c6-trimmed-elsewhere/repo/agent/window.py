"""Shape the turn list into the payload the model client expects."""

KEEP_LAST = 20


def fit(turns):
    # Older turns are dropped from the session itself, not only from the copy.
    del turns[:-KEEP_LAST]
    return [{"role": t["role"], "content": t["content"]} for t in turns]
