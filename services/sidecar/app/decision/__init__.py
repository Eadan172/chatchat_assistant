"""Jev decision layer.

M2 records Choice / Score / Noul probes in shadow mode. The real endpoint is
not selected; callers depend on DecisionClient, and the process uses the
local rule-based placeholder.
"""
