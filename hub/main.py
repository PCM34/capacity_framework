"""Entry point for the Capacity Hub desktop app.

Run via launch.bat (which invokes `python -m hub.main`), or directly with
`python -m hub.main` from the project root once the venv is set up.

To add a new tool: write its logic in hub/tools/<name>.py (plain functions,
no UI imports), wrap it in a hub/pages/<name>_page.py module that calls
@register(...) on a render() function (see capacity_model_page.py or
etl_page.py for the pattern), then add it to the import list below so it
registers itself on startup.
"""
from nicegui import ui

# Side-effecting imports: each of these registers a tool via @register.
from hub.pages import capacity_model_page, etl_page  # noqa: F401
from hub.pages.home import render_home
from hub.core import registry

APP_TITLE = 'Capacity Hub'


@ui.page('/')
def main_page() -> None:
    ui.colors(primary='#2563eb')
    content = ui.column().classes('w-full max-w-7xl mx-auto p-6 gap-4')

    def show(key: str | None) -> None:
        content.clear()
        with content:
            if key is None:
                render_home(registry.all_tools(), on_select=show)
                return
            tool = registry.get(key)
            if tool is None:
                ui.label(f"Unknown tool: {key}").classes('text-red-500')
                return
            ui.label(tool.title).classes('text-2xl font-bold')
            if tool.description:
                ui.label(tool.description).classes('text-gray-500 mb-2')
            tool.render()

    with ui.header().classes('items-center justify-between px-4'):
        with ui.row().classes('items-center gap-2'):
            ui.icon('hub').classes('text-2xl')
            ui.label(APP_TITLE).classes('text-xl font-semibold')
        ui.button(icon='home', on_click=lambda: show(None)).props('flat round color=white')

    with ui.left_drawer().classes('bg-slate-50'):
        ui.label('Tools').classes('text-xs font-semibold text-gray-500 px-2 pt-2 uppercase tracking-wide')
        for tool in registry.all_tools():
            ui.button(tool.title, icon=tool.icon, on_click=lambda t=tool: show(t.key)) \
                .props('flat align=left no-caps').classes('w-full justify-start')

    show('capacity_model')


def main() -> None:
    ui.run(
        native=True,
        window_size=(1280, 820),
        title=APP_TITLE,
        reload=False,
        show=True,
    )


if __name__ in {'__main__', '__mp_main__'}:
    main()
