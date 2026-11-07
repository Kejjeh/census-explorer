"""Census Explorer: a local, offline-first explorer for official census data.

Importing this package must never perform network access, read credentials, or
write outside an explicitly requested output directory.  Retrieval is an
explicit command (see ``census_explorer.cli``).
"""

__version__ = "0.1.0"

# Intentionally no eager imports of retrieval modules: importing the package
# must stay free of side effects.
