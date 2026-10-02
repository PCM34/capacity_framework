"""Shared helpers for tool pages: native save-file dialog for DataFrame output.

Centralized here because every tool page needs the same "save my results"
button, and it must go through the native OS dialog (there's no browser tab
to drive a normal download in native window mode).
"""
import pandas as pd
import webview
from nicegui import app, ui


async def save_dataframe_dialog(df: pd.DataFrame, default_filename: str) -> None:
    """Open a native Save As dialog and write df to the chosen path as CSV."""
    main_window = app.native.main_window
    if main_window is None:
        ui.notify('Save dialog is only available when running as a native app.', type='warning')
        return

    result = await main_window.create_file_dialog(
        dialog_type=webview.FileDialog.SAVE,
        save_filename=default_filename,
        file_types=('CSV Files (*.csv)', 'All files (*.*)'),
    )
    if not result:
        return  # user canceled

    path = result[0] if isinstance(result, (tuple, list)) else result
    if not path.lower().endswith('.csv'):
        path += '.csv'

    df.to_csv(path, index=False)
    ui.notify(f'Saved to {path}', type='positive')
