from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import dokey
from dokey import cli
from dokey.commands import runtime


class CliModuleBoundaryTests(unittest.TestCase):
    def test_facade_keeps_the_existing_call_surface(self) -> None:
        self.assertEqual(cli.main.__module__, "dokey.cli")
        for name in (
            "build_parser",
            "ingest",
            "ingest_entries",
            "launch_default",
            "main_app",
            "open_reader",
            "resolve_lake",
            "run_auto",
            "run_flow_ingest",
            "run_folios",
            "run_hwp_ingest",
            "run_md_ingest",
            "run_sheet_ingest",
        ):
            self.assertTrue(callable(getattr(cli, name)), name)

    def test_ui_command_resolves_the_package_entry_file(self) -> None:
        with (
            mock.patch.object(
                runtime.importlib.util, "find_spec", return_value=object()
            ),
            mock.patch.object(runtime.subprocess, "call", return_value=0) as call,
        ):
            runtime.run_ui(SimpleNamespace(lake=None, port=None))

        command = call.call_args.args[0]
        entry_file = Path(command[command.index("run") + 1]).resolve()
        expected = Path(dokey.__file__).resolve().parent / "ui_app.py"
        self.assertEqual(entry_file, expected)
        self.assertTrue(entry_file.is_file())


if __name__ == "__main__":
    unittest.main()
