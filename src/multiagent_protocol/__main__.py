"""Enable ``python -m multiagent_protocol`` to run the CLI.

``python -m multiagent_protocol <args>`` executes this module, delegating to
the same argument parser as the ``multiagent-protocol`` console script
(:func:`multiagent_protocol.cli.main`). The bot-cron workflow invokes
``python -m multiagent_protocol tick``; without this module that invocation
fails with ``No module named multiagent_protocol.__main__``.
"""

from __future__ import annotations

from multiagent_protocol.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
