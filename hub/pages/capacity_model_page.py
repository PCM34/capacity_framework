"""Single-page capacity model workflow: load the input workbook, run the
model, and review workcenter utilization / hours outputs -- all in one tool.

This absorbs what used to be two separate tools (a demo capacity model and
a standalone workbook viewer): the data-loading controls and per-sheet
viewer now live inside the "Run Model" tab here.
"""
from pathlib import Path

import pandas as pd
import webview
from nicegui import app, background_tasks, run, ui

from hub.core.display import for_table_display
from hub.core.io_helpers import save_workbook_dialog
from hub.core.paths import DATA_DIR
from hub.core.registry import register
from hub.tools.capacity_model import run_capacity_model, workcenter_program_breakdown
from hub.tools.excel_io import load_workbook, open_in_default_app

EXAMPLE_FILE = DATA_DIR / 'ExampleExcelFile.xlsx'


def _build_stacked_chart(breakdown: pd.DataFrame, title: str, y_label: str,
                          available_hours: float | None = None) -> dict:
    """breakdown: index=Month, columns=Program, values=numeric -> a stacked-area ("sand") chart."""
    months = [m.strftime('%Y-%m') for m in breakdown.index]
    traces = [
        {
            'x': months,
            'y': breakdown[program].tolist(),
            'type': 'scatter',
            'mode': 'lines',
            'stackgroup': 'one',
            'name': str(program),
        }
        for program in breakdown.columns
    ]
    if available_hours is not None:
        traces.append({
            'x': months,
            'y': [available_hours] * len(months),
            'type': 'scatter',
            'mode': 'lines',
            'name': 'Available Hours',
            'line': {'dash': 'dot', 'color': '#333333'},
        })
    return {
        'data': traces,
        'layout': {
            'title': title,
            'yaxis': {'title': y_label},
            'margin': {'t': 40, 'l': 55, 'r': 20, 'b': 40},
            'legend': {'orientation': 'h', 'y': -0.25},
        },
    }


@register(
    key='capacity_model',
    title='Capacity Model',
    icon='calculate',
    description='Load the input workbook, run the capacity model, and review workcenter utilization.',
)
def render() -> None:
    state: dict = {
        'path': EXAMPLE_FILE if EXAMPLE_FILE.exists() else None,
        'sheets': None,
        'result': None,
    }

    with ui.tabs().classes('w-full') as top_tabs:
        run_tab = ui.tab('Run Model')
        output_tab = ui.tab('Model Outputs')

    with ui.tab_panels(top_tabs, value=run_tab).classes('w-full'):

        # ---------------------------------------------------------------- Run Model
        with ui.tab_panel(run_tab):
            file_label = ui.label().classes('text-sm text-gray-500')
            sheets_container = ui.column().classes('w-full gap-2 mt-2')

            def refresh_controls() -> None:
                file_label.text = f'Current file: {state["path"]}' if state['path'] else 'No file selected yet.'
                refresh_button.enabled = state['path'] is not None
                run_button.enabled = state['sheets'] is not None

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
                state['sheets'] = sheets
                state['result'] = None
                reset_outputs()
                refresh_controls()
                sheets_container.clear()
                with sheets_container:
                    with ui.tabs().classes('w-full') as data_tabs:
                        tab_refs = {name: ui.tab(name) for name in sheets}
                    first_tab = next(iter(tab_refs.values()), None)
                    with ui.tab_panels(data_tabs, value=first_tab).classes('w-full'):
                        for name, df in sheets.items():
                            with ui.tab_panel(tab_refs[name]):
                                ui.label(f'{len(df)} rows x {len(df.columns)} columns').classes(
                                    'text-xs text-gray-500 mb-1')
                                ui.table.from_pandas(for_table_display(df), pagination=15).classes('w-full')

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

            async def on_run_model() -> None:
                if not state['sheets']:
                    ui.notify('Load a workbook first.', type='warning')
                    return
                run_button.disable()
                try:
                    result = await run.cpu_bound(run_capacity_model, state['sheets'])
                except Exception as exc:  # noqa: BLE001 - surface any model error to the user
                    ui.notify(f'Model failed: {exc}', type='negative')
                    return
                finally:
                    run_button.enabled = state['sheets'] is not None
                state['result'] = result
                populate_outputs(result)
                top_tabs.set_value(output_tab)
                ui.notify('Model run complete.', type='positive')

            with ui.row().classes('items-center gap-2'):
                ui.button('Open in Excel', icon='open_in_new', on_click=open_current_in_excel).props('outline')
                refresh_button = ui.button('Refresh', icon='refresh', on_click=refresh_current).props('outline')
                ui.button('Choose a different file...', icon='folder_open', on_click=choose_file).props('outline')
            with ui.row().classes('items-center gap-2 mt-3'):
                run_button = ui.button('Run Capacity Model', icon='play_arrow', on_click=on_run_model)
            refresh_controls()

        # ------------------------------------------------------------- Model Outputs
        with ui.tab_panel(output_tab):
            outputs_container = ui.column().classes('w-full gap-4')

            def reset_outputs() -> None:
                outputs_container.clear()
                with outputs_container:
                    ui.label('Run the model from the "Run Model" tab to see results here.').classes(
                        'text-gray-500')

            reset_outputs()

    def build_workcenter_subtab(pivot: pd.DataFrame, raw_output: pd.DataFrame,
                                 available_hours_by_workcenter: dict, metric_col: str,
                                 metric_title: str, y_label: str, show_available_line: bool) -> None:
        ui.label('All workcenters by month').classes('text-sm font-medium')
        ui.table.from_pandas(for_table_display(pivot.reset_index()), pagination=5).classes('w-full')

        workcenters = list(pivot.index)
        if not workcenters:
            ui.label('No workcenters to chart.').classes('text-gray-500')
            return

        ui.label('Breakdown by program for one workcenter').classes('text-sm font-medium mt-4')

        def draw_chart(workcenter: str) -> None:
            chart_container.clear()
            breakdown = workcenter_program_breakdown(raw_output, workcenter, metric_col)
            available_hours = available_hours_by_workcenter.get(workcenter) if show_available_line else None
            fig = _build_stacked_chart(breakdown, f'{workcenter} {metric_title} by Month', y_label,
                                        available_hours)
            with chart_container:
                ui.plotly(fig).classes('w-full h-80')

        ui.select(workcenters, value=workcenters[0], with_input=True,
                  on_change=lambda e: draw_chart(e.value)).classes('w-64')
        chart_container = ui.column().classes('w-full')
        draw_chart(workcenters[0])

    async def export_results() -> None:
        result = state['result']
        if not result:
            ui.notify('Run the model first.', type='warning')
            return
        sheets = {
            'Raw Capacity Output': result['raw_output'],
            'Workcenter Utilization': result['util_pivot'].reset_index(),
            'Workcenter Hours Load': result['hours_pivot'].reset_index(),
        }
        await save_workbook_dialog(sheets, 'capacity_model_results.xlsx')

    def populate_outputs(result: dict) -> None:
        outputs_container.clear()
        with outputs_container:
            with ui.row().classes('items-center justify-between w-full'):
                ui.label('Model results').classes('text-lg font-medium')
                ui.button('Export results to Excel', icon='download', on_click=export_results)

            with ui.tabs().classes('w-full') as sub_tabs:
                raw_tab = ui.tab('Raw Capacity Output')
                util_tab = ui.tab('Workcenter Utilization')
                hours_tab = ui.tab('Workcenter Hours Load')
            with ui.tab_panels(sub_tabs, value=raw_tab).classes('w-full'):
                with ui.tab_panel(raw_tab):
                    ui.table.from_pandas(for_table_display(result['raw_output']), pagination=25).classes('w-full')
                with ui.tab_panel(util_tab):
                    build_workcenter_subtab(
                        result['util_pivot'], result['raw_output'], result['available_hours_by_workcenter'],
                        'Util_Increment', 'Utilization', 'Utilization (sum of increments)',
                        show_available_line=False,
                    )
                with ui.tab_panel(hours_tab):
                    build_workcenter_subtab(
                        result['hours_pivot'], result['raw_output'], result['available_hours_by_workcenter'],
                        'Required_Hours', 'Load Hours', 'Required Hours',
                        show_available_line=True,
                    )

    if state['path']:
        background_tasks.create(load_and_show(state['path']), name='load_initial_workbook')
