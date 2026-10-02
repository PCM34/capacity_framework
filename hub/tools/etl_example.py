"""Demo ETL: clean up an arbitrary CSV export.

Pure business logic, no UI imports - swap this out for a real ETL and the
page in hub/pages/etl_page.py barely has to change.
"""
import pandas as pd


def transform(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names, drop empty rows/columns, strip whitespace."""
    df = raw.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    df = df.dropna(how="all").dropna(axis=1, how="all")

    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip()

    return df.reset_index(drop=True)
