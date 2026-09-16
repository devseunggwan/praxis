"""Slug helpers. Pure string work — no I/O, no shared state."""

SEPARATOR = "-"


def slugify(title):
    kept = [char.lower() if char.isalnum() else " " for char in title]
    return SEPARATOR.join("".join(kept).split())
