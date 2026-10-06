"""Shared helpers for tool pages: native save-file dialogs for output data.

Centralized here because every tool page needs the same "save my results"
button, and it must go through the native OS dialog (there's no browser tab
to drive a normal download in native window mode).
"""
from collections.abc import Mapping

import pandas as pd
import webview
from nicegui import app, run, ui


async def _prompt_save_path(default_filename: str, file_types: tuple[str, ...], extension: str) -> str | None:
    main_window = app.native.main_window
    if main_window is None:
        ui.notify('Save dialog is only available when running as a native app.', type='warning')
        return None

    result = await main_window.create_file_dialog(
        dialog_type=webview.FileDialog.SAVE,
        save_filename=default_filename,
        file_types=file_types,
    )
    if not result:
        return None  # user canceled

    path = result[0] if isinstance(result, (tuple, list)) else result
    if not path.lower().endswith(extension):
        path += extension
    return path


async def save_dataframe_dialog(df: pd.DataFrame, default_filename: str) -> None:
    """Open a native Save As dialog and write df to the chosen path as CSV."""
    path = await _prompt_save_path(default_filename, ('CSV Files (*.csv)', 'All files (*.*)'), '.csv')
    if path is None:
        return
    df.to_csv(path, index=False)
    ui.notify(f'Saved to {path}', type='positive')


async def save_workbook_dialog(sheets: Mapping[str, pd.DataFrame], default_filename: str) -> None:
    """Open a native Save As dialog and write multiple DataFrames to one .xlsx, one sheet each."""
    path = await _prompt_save_path(default_filename, ('Excel Files (*.xlsx)', 'All files (*.*)'), '.xlsx')
    if path is None:
        return

    def _write() -> None:
        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            for sheet_name, df in sheets.items():
                df.to_excel(writer, sheet_name=sheet_name[:31], index=False)

    await run.io_bound(_write)
    ui.notify(f'Saved to {path}', type='positive')
