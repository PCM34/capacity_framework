"""UI wrapper around hub.tools.excel_io: open a workbook in Excel itself, or
browse its sheets inline, one tab per sheet.
"""
from datetime import datetime
from pathlib import Path

import pandas as pd
import webview
from nicegui import app, background_tasks, run, ui

from hub.core.paths import DATA_DIR
from hub.core.registry import register
from hub.tools.excel_io import load_workbook, open_in_default_app

EXAMPLE_FILE = DATA_DIR / 'ExampleExcelFile.xlsx'


def _for_display(df: pd.DataFrame) -> pd.DataFrame:
    """ui.table needs string column names; Excel date headers come in as datetimes."""
    display = df.copy()
    display.columns = [c.strftime('%Y-%m-%d') if isinstance(c, datetime) else str(c) for c in display.columns]
    return display


@register(
    key='excel_viewer',
    title='Workbook Viewer',
    icon='table_view',
    description='Open the example input workbook in Excel, or browse its tabs here.',
)
def render() -> None:
    state: dict[str, Path | None] = {'path': EXAMPLE_FILE if EXAMPLE_FILE.exists() else None}

    file_label = ui.label().classes('text-sm text-gray-500')
    sheets_container = ui.column().classes('w-full gap-2 mt-4')

    def refresh_label() -> None:
        if not state['path']:
            file_label.text = 'No file selected yet.'
        else:
            loaded_at = datetime.now().strftime('%I:%M:%S %p')
            file_label.text = f'Current file: {state["path"]}  (last loaded {loaded_at})'
        refresh_button.enabled = state['path'] is not None

    async def load_and_show(path: Path) -> None:
        sheets_container.clear()
        with sheets_container:
            ui.spinner(size='lg')
        try:
            sheets = await run.io_bound(load_workbook, path)
        except Exception as exc:  # noqa: BLE001 - surface any read error to the user
            sheets_container.clear()
            ui.notify(f'Could not read workbook: {exc}', type='negative')
            return

        state['path'] = path
        refresh_label()
        sheets_container.clear()
        with sheets_container:
            with ui.tabs().classes('w-full') as tabs:
                tab_refs = {name: ui.tab(name) for name in sheets}
            first_tab = next(iter(tab_refs.values()), None)
            with ui.tab_panels(tabs, value=first_tab).classes('w-full'):
                for name, df in sheets.items():
                    with ui.tab_panel(tab_refs[name]):
                        ui.label(f'{len(df)} rows x {len(df.columns)} columns').classes(
                            'text-xs text-gray-500 mb-1')
                        ui.table.from_pandas(_for_display(df), pagination=15).classes('w-full')

    def open_current_in_excel() -> None:
        if not state['path']:
            ui.notify('No file selected yet.', type='warning')
            return
        try:
            open_in_default_app(state['path'])
        except Exception as exc:  # noqa: BLE001
            ui.notify(f'Could not open file: {exc}', type='negative')

    async def refresh_current() -> None:
        if not state['path']:
            ui.notify('No file selected yet.', type='warning')
            return
        await load_and_show(state['path'])
        ui.notify('Reloaded from disk.', type='positive')

    async def choose_file() -> None:
        main_window = app.native.main_window
        if main_window is None:
            ui.notify('File picker is only available when running as a native app.', type='warning')
            return
        result = await main_window.create_file_dialog(
            dialog_type=webview.FileDialog.OPEN,
            file_types=('Excel Files (*.xlsx;*.xlsm)', 'All files (*.*)'),
        )
        if not result:
            return
        await load_and_show(Path(result[0]))

    with ui.row().classes('items-center gap-2'):
        ui.button('Open in Excel', icon='open_in_new', on_click=open_current_in_excel)
        refresh_button = ui.button('Refresh', icon='refresh', on_click=refresh_current)
        ui.button('Choose a different file...', icon='folder_open', on_click=choose_file)
    refresh_label()

    if state['path']:
        background_tasks.create(load_and_show(state['path']), name='load_initial_workbook')
