"""UI wrapper around hub.tools.etl_example. Thin by design -- all the real
logic lives in the tools module and stays usable/testable on its own.
"""
from io import BytesIO

import pandas as pd
from nicegui import events, run, ui

from hub.core.io_helpers import save_dataframe_dialog
from hub.core.registry import register
from hub.tools.etl_example import transform


@register(
    key='etl_example',
    title='CSV Cleanup ETL',
    icon='table_chart',
    description='Upload a CSV export and get back a cleaned, normalized version.',
)
def render() -> None:
    results = ui.column().classes('w-full gap-3 mt-4')

    async def handle_upload(e: events.UploadEventArguments) -> None:
        results.clear()
        with results:
            ui.spinner(size='lg')
        try:
            raw_bytes = await e.file.read()
            raw_df = await run.io_bound(pd.read_csv, BytesIO(raw_bytes))
            cleaned = await run.io_bound(transform, raw_df)
        except Exception as exc:  # noqa: BLE001 - surface any ETL error to the user
            results.clear()
            ui.notify(f'Could not process file: {exc}', type='negative')
            return

        results.clear()
        with results:
            ui.label(f'{len(cleaned)} rows x {len(cleaned.columns)} columns after cleanup').classes(
                'text-sm text-gray-500')
            ui.table.from_pandas(cleaned.head(50), pagination=10).classes('w-full')
            ui.button(
                'Save cleaned CSV',
                icon='download',
                on_click=lambda: save_dataframe_dialog(cleaned, 'cleaned_output.csv'),
            )

    with ui.card().classes('w-full'):
        ui.label('Upload a CSV to clean').classes('text-lg font-medium')
        ui.upload(on_upload=handle_upload, auto_upload=True).props('accept=.csv').classes('w-full')
