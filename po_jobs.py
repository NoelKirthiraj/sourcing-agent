"""Backwards-compatible alias for the generic job store.

PO extraction was the first user of this store; external supplier search is
the second, so the implementation moved to jobs.py.

This is a true module alias rather than a re-export. A `from jobs import *`
shim would copy module-level constants, so monkeypatching `po_jobs.TTL_SECONDS`
in a test would silently not reach the `_cleanup` that reads `jobs.TTL_SECONDS`.
Rebinding sys.modules makes `po_jobs is jobs`, so every attribute — including
patched ones — resolves to the same object.
"""
import sys

import jobs

sys.modules[__name__] = jobs
