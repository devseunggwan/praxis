"""Display labels built from slugs."""

from .slug import slugify


def label_for(title):
    return slugify(title).replace("-", " ").title()
