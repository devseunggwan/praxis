"""Recent-event report."""

PAGE_SIZE = 10


def render(rows):
    return [f"{row['at']} {row['kind']}" for row in rows]
