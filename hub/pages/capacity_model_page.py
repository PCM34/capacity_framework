"""UI wrapper around hub.tools.capacity_model. Thin by design -- all the
real logic lives in the tools module and stays usable/testable on its own.
"""
from nicegui import run, ui

from hub.core.io_helpers import save_dataframe_dialog
from hub.core.registry import register
from hub.tools.capacity_model import CapacityInputs, run_capacity_model


@register(
    key='capacity_model',
    title='Capacity Model',
    icon='calculate',
    description='Estimate required headcount from volume, AHT, shrinkage, and target service level.',
)
def render() -> None:
    with ui.card().classes('w-full'):
        ui.label('Inputs').classes('text-lg font-medium')
        with ui.grid(columns=2).classes('w-full gap-x-6 gap-y-2'):
            volume = ui.number('Volume per day', value=1000, min=0)
            aht = ui.number('Average handle time (sec)', value=300, min=1)
            shrinkage = ui.number('Shrinkage %', value=30, min=0, max=99)
            target_sl = ui.number('Target service level %', value=80, min=1, max=100)
            awt = ui.number('Target answer time (sec)', value=20, min=1)
            hours = ui.number('Hours per agent per day', value=8, min=1, max=24)
        run_button = ui.button('Run model', icon='play_arrow').classes('mt-3')

    results = ui.column().classes('w-full gap-3 mt-4')

    async def on_run() -> None:
        run_button.disable()
        results.clear()
        with results:
            ui.spinner(size='lg')
        try:
            inputs = CapacityInputs(
                volume_per_day=volume.value,
                aht_seconds=aht.value,
                shrinkage_pct=shrinkage.value,
                target_service_level_pct=target_sl.value,
                target_answer_time_sec=awt.value,
                hours_per_agent_per_day=hours.value,
            )
            result = await run.cpu_bound(run_capacity_model, inputs)
        except Exception as exc:  # noqa: BLE001 - surface any model error to the user
            results.clear()
            ui.notify(f'Model failed: {exc}', type='negative')
            return
        finally:
            run_button.enable()

        table_df = result['sensitivity_table']
        results.clear()
        with results:
            with ui.row().classes('gap-4'):
                with ui.card():
                    ui.label('Required agents (pre-shrinkage)').classes('text-sm text-gray-500')
                    ui.label(str(result['required_agents'])).classes('text-3xl font-bold')
                with ui.card():
                    ui.label('Required FTE (post-shrinkage)').classes('text-sm text-gray-500')
                    ui.label(str(result['required_fte'])).classes('text-3xl font-bold')

            fig = {
                'data': [{
                    'x': table_df['volume_per_day'].tolist(),
                    'y': table_df['required_fte'].tolist(),
                    'type': 'scatter',
                    'mode': 'lines+markers',
                }],
                'layout': {'title': 'Required FTE vs. volume', 'margin': {'t': 40, 'l': 40, 'r': 20, 'b': 40}},
            }
            ui.plotly(fig).classes('w-full h-64')

            ui.label('Volume sensitivity').classes('text-sm font-medium')
            ui.table.from_pandas(table_df, pagination=10).classes('w-full')

            ui.button(
                'Save results as CSV',
                icon='download',
                on_click=lambda: save_dataframe_dialog(table_df, 'capacity_model_results.csv'),
            )

    run_button.on_click(on_run)
