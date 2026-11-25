"""One chat session with the model."""
from agent.window import fit


class Session:
    def __init__(self, client):
        self.client = client
        self.turns = []

    def say(self, text):
        self.turns.append({"role": "user", "content": text})
        reply = self.client.complete(fit(self.turns))
        self.turns.append({"role": "assistant", "content": reply})
        return reply
