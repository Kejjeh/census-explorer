"""Provider-specific retrieval.

Each module here turns an explicit user command into immutable cached bytes
plus a :class:`census_explorer.provenance.RetrievalRecord`.  No module in this
package performs network access at import time.
"""
