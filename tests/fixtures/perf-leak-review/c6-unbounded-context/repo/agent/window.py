"""Shape the turn list into the payload the model client expects."""


def fit(turns):
    return [{"role": t["role"], "content": t["content"]} for t in turns]
