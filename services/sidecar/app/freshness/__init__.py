"""Local freshness checks for imported documents.

M4 re-reads a file that is already on disk and compares content hash and
mtime. Live web re-fetch and connector refresh are left for later milestones.
"""
