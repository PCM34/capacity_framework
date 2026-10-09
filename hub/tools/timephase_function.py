import pandas as pd

def apply_timephase(df1: pd.DataFrame, df2: pd.DataFrame,
                     keys1: list[str], keys2: list[str],
                     date1: str, date2: str,
                     val1: str, val2: str, tp_param: str) -> pd.DataFrame:
    """Override df1[val1] with the most recent df2[val2] where keys match
    and df1[date1] >= df2[date2].

    The shape and column order of df1 are preserved.
    """
    df1 = df1.copy()
    df2 = df2.copy()
    # Preserve original dtypes of the key columns for later restoration
    original_key_dtypes = {k: df1[k].dtype for k in keys1}
    # Preserve original dtype of the value column to be overridden
    original_val_dtype = df1[val1].dtype if val1 in df1.columns else None
    # Filter df2 to rows matching the requested time phase parameter
    if 'TP Parameter' in df2.columns:
        df2 = df2[df2['TP Parameter'] == tp_param]
    df1[date1] = pd.to_datetime(df1[date1])
    df1['_orig_idx'] = df1.index
    df1_sorted = df1.sort_values(date1)
    df2[date2] = pd.to_datetime(df2[date2]).astype(df1[date1].dtype)
    df2_subset = df2[[*keys2, date2, val2]].copy()
    rename_map = dict(zip(keys2, keys1))
    if date1 == date2:
        tmp_date = f"{date2}_tp"
        rename_map[date2] = tmp_date
    else:
        tmp_date = date2
        rename_map[date2] = date2
    df2_subset = df2_subset.rename(columns=rename_map)
    # Ensure key columns have matching dtypes (cast to string)
    for k in keys1:
        df1[k] = df1[k].astype(str)
    for k in keys2:
        df2_subset[k] = df2_subset[k].astype(str)
    df2_subset = df2_subset.sort_values(tmp_date)
    df1_sorted = df1.sort_values(date1)
    df_merge = pd.merge_asof(
        df1_sorted,
        df2_subset,
        left_on=date1,
        right_on=tmp_date,
        by=keys1,
        direction="backward",
        suffixes=("", "_tp"),
    )
    if f"{val2}_tp" in df_merge.columns:
        val2_col = f"{val2}_tp"
    else:
        val2_col = val2
    # Ensure the target column can hold the new values (e.g., float when overriding int)
    df_merge[val1] = df_merge[val1].astype(float)
    mask = df_merge[val2_col].notna()
    if mask.any():
        df_merge.loc[mask, val1] = df_merge.loc[mask, val2_col]
    if date1 == date2:
        df_merge = df_merge.drop(columns=[tmp_date])
    # Restore original key column dtypes
    for k, dtype in original_key_dtypes.items():
        df_merge[k] = df_merge[k].astype(dtype)
        if k == 'Router':
            df_merge[k] = df_merge[k].apply(lambda x: int(x) if isinstance(x, str) and str(x).isdigit() else x)

    return df_merge[df1.columns]
