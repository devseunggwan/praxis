"""Event table access."""


class EventTable:
    """Every accessor issues one statement against the events table."""

    def __init__(self, connection):
        self._connection = connection

    def fetch_all(self):
        return self._connection.select("select id, kind, at from events")

    def select_where(self, kind, since, limit):
        """Pushes the predicate, the ordering, and the limit into the query."""
        return self._connection.select(
            "select id, kind, at from events where kind = ? and at > ?"
            " order by at desc limit ?",
            [kind, since, limit],
        )
