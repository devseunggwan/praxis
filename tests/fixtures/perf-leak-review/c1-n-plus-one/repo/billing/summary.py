"""Monthly invoice summary."""


def total_paid(rows):
    return sum(row["total"] for row in rows if row["paid_at"] is not None)
