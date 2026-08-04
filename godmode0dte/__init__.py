"""GodMode0DTE — extremely selective, defined-risk 0DTE debit-vertical system.

Layering (imports may only point left-to-right; execution is unreachable
from scoring — only the RiskGovernor may touch the broker):

    data -> features -> regime -> scoring -> risk -> execution -> monitoring
"""

__version__ = "0.1.0"
