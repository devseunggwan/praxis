"""Storage access for invoice records."""


class InvoiceStore:
    """Reads invoice rows. Both accessors hit the same backing table."""

    def __init__(self, connection):
        self._connection = connection

    def fetch_one(self, invoice_id):
        return self._connection.select(
            "select id, total, paid_at from invoice where id = ?", [invoice_id]
        )

    def fetch_many(self, invoice_ids):
        """Batched sibling of fetch_one — one round trip for any id count."""
        placeholders = ",".join("?" for _ in invoice_ids)
        return self._connection.select(
            f"select id, total, paid_at from invoice where id in ({placeholders})",
            list(invoice_ids),
        )
