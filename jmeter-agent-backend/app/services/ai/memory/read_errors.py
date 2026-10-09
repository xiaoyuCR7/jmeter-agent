"""Opt-in read failure reporting; legacy empty results and cancellation stay intact."""
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class LedgerReadError(RuntimeError):
    def __init__(self, ledger: str, operation: str):
        self.ledger = ledger
        self.operation = operation
        super().__init__('Knowledge ledger read failed')


@contextmanager
def ledger_read_boundary(strict: bool, ledger: str, operation: str):
    try:
        yield
    except Exception as error:
        if not strict:
            raise
        # SQL, filesystem paths and driver messages must not enter public errors/logs.
        logger.error('Ledger read failed: %s/%s (%s)', ledger, operation, type(error).__name__)
        raise LedgerReadError(ledger, operation) from error
