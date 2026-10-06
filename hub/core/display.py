"""Make DataFrames safe and clean for ui.table.from_pandas.

Needed because ui.table serializes column headers as JSON object keys:
a datetime header (e.g. a pivot table's Month columns) crashes with
"Dict key must be str" unless stringified first. Datetime *value* columns
are handled by NiceGUI already, but it renders full timestamps, so we
reformat those too for a cleaner look.
"""
from datetime import datetime

import pandas as pd


def for_table_display(df: pd.DataFrame) -> pd.DataFrame:
    display = df.copy()
    display.columns = [c.strftime('%Y-%m-%d') if isinstance(c, datetime) else str(c) for c in display.columns]
    for col in display.columns:
        if pd.api.types.is_datetime64_any_dtype(display[col]):
            display[col] = display[col].dt.strftime('%Y-%m-%d')
    return display
