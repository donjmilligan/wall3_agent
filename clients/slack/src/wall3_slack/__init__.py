"""wall3_slack: a Slack Web API client and CLI that acts as the owner via a user token.

Two layers, kept apart on purpose:

* ``extract/`` talks to the network and writes raw responses verbatim to disk.
* ``normalize/`` reads a raw directory and produces contract ``Message`` objects,
  with no network access (enforced by ``tests/test_normalize_boundary.py``).
"""

__version__ = "0.1.0"
