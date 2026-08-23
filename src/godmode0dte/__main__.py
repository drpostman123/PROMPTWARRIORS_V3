"""Entry point: ``python -m godmode0dte`` or the ``godmode`` script."""

from __future__ import annotations

import argparse
import asyncio

from godmode0dte.app import GodModeApp
from godmode0dte.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="GodMode0DTE trading runtime")
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    app = GodModeApp(cfg)
    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        print("shutdown requested")


if __name__ == "__main__":
    main()
