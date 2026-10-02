"""Reading Excel workbooks and opening them in the OS default app (Excel).

Pure I/O helpers, no UI imports - the capacity model will eventually build on
load_workbook() directly once we've worked out how to interpret the sheets.
"""
import os
import platform
from pathlib import Path

import pandas as pd


def load_workbook(path: str | Path) -> dict[str, pd.DataFrame]:
    """Read every sheet of an Excel workbook into a dict of {sheet_name: DataFrame}."""
    return pd.read_excel(path, sheet_name=None, engine='openpyxl')


def open_in_default_app(path: str | Path) -> None:
    """Open a file with whatever application the OS has associated with it (e.g. Excel)."""
    path = str(path)
    system = platform.system()
    if system == 'Windows':
        os.startfile(path)  # type: ignore[attr-defined]
    elif system == 'Darwin':
        os.system(f'open "{path}"')
    else:
        os.system(f'xdg-open "{path}"')
