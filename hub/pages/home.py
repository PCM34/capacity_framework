"""Landing page: a card per registered tool. Not itself a registered tool --
main.py shows this by default when nothing else is selected.
"""
from typing import Callable

from nicegui import ui

from hub.core.registry import Tool


def render_home(tools: list[Tool], on_select: Callable[[str], None]) -> None:
    ui.label('Welcome').classes('text-2xl font-bold')
    ui.label('Pick a tool from the sidebar, or a card below, to get started.').classes('text-gray-500 mb-4')

    with ui.row().classes('w-full gap-4 flex-wrap'):
        for tool in tools:
            with ui.card().tight().classes('w-64 cursor-pointer hover:shadow-lg transition-shadow') \
                    .on('click', lambda t=tool: on_select(t.key)):
                with ui.card_section():
                    ui.icon(tool.icon).classes('text-3xl text-primary')
                    ui.label(tool.title).classes('text-lg font-medium mt-1')
                    if tool.description:
                        ui.label(tool.description).classes('text-sm text-gray-500')
