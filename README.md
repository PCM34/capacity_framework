# Capacity Hub

A single desktop app (NiceGUI, native window) that wraps your Python scripts
in a sidebar of tools, so your team runs them by double-clicking a shortcut
instead of opening an IDE. No server, no hosting — it's a local process on
each person's own machine.

## For teammates: first-time setup

1. Copy this whole folder to your machine (or pull it from wherever it's shared).
2. Double-click **`setup_env.bat`**. This creates a private Python environment
   in `.venv` and installs the pinned dependencies from `requirements.txt`.
   Needs Python 3.10+ already available on PATH — ask IT if `python --version`
   fails in a terminal.
3. Double-click **`launch.bat`** any time after that to open the app.

You only need to re-run `setup_env.bat` if `requirements.txt` changes.

## Project layout

```
hub/
  main.py            entry point - builds the window, sidebar, and routing
  core/
    registry.py       plugin registry tools register themselves into
    io_helpers.py      shared native "save as" dialogs (single CSV or multi-sheet Excel)
    display.py         ui.table-safe DataFrame formatting (stringifies date headers, etc.)
    paths.py           PROJECT_ROOT / DATA_DIR constants
  tools/               pure business logic, no UI code - script authors live here
    capacity_model.py    the real demand -> BOM -> routing -> utilization pipeline
    etl_example.py       demo CSV cleanup, still illustrative
    excel_io.py          read a workbook's sheets, open a file in its default app
  pages/               thin NiceGUI wrappers: inputs/outputs around a tools/ module
    capacity_model_page.py   single page: "Run Model" (load workbook + run) / "Model Outputs"
    etl_page.py
    home.py
data/
  ExampleExcelFile.xlsx   sample input workbook for the capacity model
```

The split matters: `hub/tools/*.py` are plain functions in, data out - you can
import and run them from a notebook or another script with zero NiceGUI
involvement. `hub/pages/*.py` only handle widgets and wiring.

## Adding your own script to the hub

1. **Write the logic** in `hub/tools/your_script.py` as plain functions,
   exactly like you would any standalone script. No NiceGUI imports here.
2. **Wrap it** in `hub/pages/your_script_page.py`:

   ```python
   from nicegui import run, ui
   from hub.core.registry import register
   from hub.tools.your_script import do_the_thing

   @register(key="your_script", title="Your Script", icon="bolt",
             description="One-line description shown on the home screen.")
   def render() -> None:
       input_value = ui.number("Some input", value=10)
       output = ui.column()

       async def on_run():
           output.clear()
           # use run.cpu_bound(...) for heavy compute, run.io_bound(...) for
           # file/network work, so the window never freezes
           result = await run.cpu_bound(do_the_thing, input_value.value)
           with output:
               ui.label(str(result))

       ui.button("Run", on_click=on_run)
   ```

3. **Register it on startup** by adding one import line in `hub/main.py`:

   ```python
   from hub.pages import capacity_model_page, etl_page, your_script_page  # noqa: F401
   ```

That's it — it shows up in the sidebar and on the home screen automatically.

## Notes

- `requirements.txt` is a full pinned snapshot (`pip freeze`) so every
  teammate installs the exact same dependency versions. After adding a new
  package, regenerate it with the venv active:
  `.venv\Scripts\python.exe -m pip freeze > requirements.txt`
- File outputs go through a native "Save As" dialog
  (`hub/core/io_helpers.py`) rather than a browser download, since this runs
  in a native window, not a browser tab.
- `hub/tools/capacity_model.py` expects a workbook with exactly these sheets:
  `Program Monthly Demand`, `BOM`, `Route Information`, `Workcenter Information`.
  All joins are inner joins, so a typo'd Part/Program/Workcenter code between
  sheets will silently drop those rows rather than error — worth spot-checking
  row counts if totals look low.
- `etl_example.py` / `etl_page.py` is still an illustrative demo — a second
  example of the tools/pages pattern, unrelated to the capacity model.
