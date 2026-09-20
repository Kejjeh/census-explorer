"""Offline test suite.

Every test in this package runs without network access and without credentials.
``helpers.offline`` asserts that: any attempt to reach a provider during a test
raises instead of downloading.
"""
