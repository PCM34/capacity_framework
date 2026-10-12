"""Manufacturing capacity model.

Pipeline: monthly program demand (wide) -> unpivot -> explode through the
BOM (quantity x make %) -> cap at Annual Max Limits -> join routing (Part
Route Map + Route Information, with optional time-phased overrides) ->
join workcenter capacity -> required hours & utilization.

Pure business logic, no UI imports, so it can be run/tested independently
of the hub.

Performance note: join_part_route_map used to allocate routes with a
per-(Month, Program, Part) groupby().apply() calling a Python function.
Benchmarked at ~1.5ms of fixed overhead per group -- fine for a handful of
parts, but a 20-program/200-part/36-month workbook (144,000 groups) took
several minutes. It's now fully vectorized (groupby().transform() only, no
per-group Python callback); the same shape of work that took 12s at 6,000
groups now takes a small fraction of that.

Assumption still in effect: "Demand_Quantity" before routing is
ceil-free, floor-based -- monthly Demand x BOM Quantity x MakePercent,
annual-capped by the largest-remainder method (floor each month, hand the
leftover units to the months with the largest fractional remainder) so a
capped part-year's total lands exactly on its Max instead of overshooting.
MakePercent is used as given -- NOT normalized to sum to 1 across a part's
BOM rows, since a value like 0.5 can legitimately mean "half of this part
is made in-house, the rest is sourced elsewhere," not "this is one of two
rows that should split 100%."
"""
import pandas as pd
import numpy as np

from hub.tools.timephase_function import apply_timephase

# ----- Configuration -----
# Default number of weeks per year used to scale workcenter available hours.
# Overridden by the front-end via set_weeks_per_year/set_overtime_hours_per_week.
_WEEKS_PER_YEAR = 50
_OVERTIME_HOURS_PER_WEEK = 24


def set_weeks_per_year(weeks: int) -> None:
    """Override the weeks-per-year factor used in `join_workcenter_information`."""
    global _WEEKS_PER_YEAR  # noqa: PLW0603
    _WEEKS_PER_YEAR = weeks


def set_overtime_hours_per_week(hours: int) -> None:
    """Override the default overtime hours per week."""
    global _OVERTIME_HOURS_PER_WEEK  # noqa: PLW0603
    _OVERTIME_HOURS_PER_WEEK = hours


REQUIRED_SHEETS = (
    'Program Monthly Demand', 'BOM', 'Annual Max Limits',
    'Part Route Map', 'Route Information', 'Workcenter Information',
)

# Columns each required sheet must have, checked up front so a typo'd/missing
# column fails fast with a clear message instead of a cryptic KeyError deep
# inside a merge. Program Monthly Demand is checked separately (its columns
# are dynamic: an id column plus however many month columns).
SHEET_SCHEMAS: dict[str, tuple[str, ...]] = {
    'BOM': ('Program', 'Part', 'Quantity', 'MakePercent'),
    'Annual Max Limits': ('Part', 'Max Annual Limit'),
    'Part Route Map': ('Part', 'Router', 'Rate', 'Limit'),
    'Route Information': ('Router', 'Operation', 'Workcenter', 'HPU', 'Sampling Rate'),
    'Workcenter Information': ('Workcenter', 'Available Hours', 'Overtime Percentage', 'Capacity Count'),
}


def _validate_workbook(sheets: dict[str, pd.DataFrame]) -> None:
    missing_sheets = [name for name in REQUIRED_SHEETS if name not in sheets]
    if missing_sheets:
        raise ValueError(f"Workbook is missing required sheet(s): {', '.join(missing_sheets)}")

    errors = [
        f"Sheet '{name}' is missing column(s): {', '.join(missing_cols)}"
        for name, required_cols in SHEET_SCHEMAS.items()
        if (missing_cols := [c for c in required_cols if c not in sheets[name].columns])
    ]
    if sheets['Program Monthly Demand'].shape[1] < 2:
        errors.append("Sheet 'Program Monthly Demand' needs an id column plus at least one month column")
    if errors:
        raise ValueError('; '.join(errors))


def unpivot_monthly_demand(program_monthly_demand: pd.DataFrame) -> pd.DataFrame:
    """Wide [Program, <month columns>] -> long [Month (date), Program (str), Demand (int)]."""
    df = program_monthly_demand.copy()
    program_col = df.columns[0]  # the index column, whatever it's labeled
    long = df.melt(id_vars=program_col, var_name='Month', value_name='Demand')
    long = long.rename(columns={program_col: 'Program'})
    long['Program'] = long['Program'].astype(str)
    long['Month'] = pd.to_datetime(long['Month']).dt.to_period('M').dt.to_timestamp()
    long['Demand'] = long['Demand'].astype(int)
    return long


def build_demand_profile(monthly_demand: pd.DataFrame, bom: pd.DataFrame,
                          annual_max_limits: pd.DataFrame) -> pd.DataFrame:
    """Explode program demand into per-part demand via the BOM (quantity-per-unit x make %),
    then cap each part's total annual production at its Annual Max Limit (if any): when the
    year's actual total would exceed the max, every month (across every program) that draws on
    that part gets scaled down by max/actual, preserving their relative proportions.
    """
    bom = bom.copy()
    bom['MakePercent'] = pd.to_numeric(bom['MakePercent'], errors='coerce').fillna(0.0)

    merged = monthly_demand.merge(bom, on='Program', how='inner')
    merged['Raw_Quantity'] = merged['Demand'] * merged['Quantity'] * merged['MakePercent']
    merged['Year'] = merged['Month'].dt.year

    annual = merged.groupby(['Part', 'Year'])['Raw_Quantity'].sum().reset_index(name='Actual')
    limits = annual_max_limits.rename(columns={'Max Annual Limit': 'Max'})
    annual = annual.merge(limits, on='Part', how='left')

    capped = annual['Max'].notna() & (annual['Max'] < annual['Actual'])
    annual['Ratio'] = 1.0
    annual.loc[capped, 'Ratio'] = annual.loc[capped, 'Max'] / annual.loc[capped, 'Actual']

    merged = merged.merge(annual[['Part', 'Year', 'Ratio']], on=['Part', 'Year'], how='left')
    scaled = merged['Raw_Quantity'] * merged['Ratio']
    # Floor first (never overshoot), then hand the leftover units to the rows with the
    # largest fractional remainder so a capped year lands exactly on its Max.
    merged['Demand_Quantity'] = np.floor(scaled).astype(int)

    # How many leftover units each capped (Part, Year) still needs, vectorized: no
    # per-group Python loop re-scanning the whole frame (that was the #1 hotspot here --
    # 60 capped part-years cost 0.65s by rescanning a 144k-row frame each time).
    annual = annual.merge(
        merged.groupby(['Part', 'Year'])['Demand_Quantity'].sum().rename('FloorSum'),
        on=['Part', 'Year'], how='left',
    )
    annual['Remainder'] = 0
    annual.loc[capped, 'Remainder'] = (annual.loc[capped, 'Max'] - annual.loc[capped, 'FloorSum']).clip(lower=0)
    merged = merged.merge(annual[['Part', 'Year', 'Remainder']], on=['Part', 'Year'], how='left')

    fraction = scaled - np.floor(scaled)
    rank_desc = fraction.groupby([merged['Part'], merged['Year']]).rank(method='first', ascending=False)
    merged['Demand_Quantity'] += ((merged['Remainder'] > 0) & (rank_desc <= merged['Remainder'])).astype(int)
    return merged[['Month', 'Program', 'Part', 'Demand_Quantity']]


def join_part_route_map(demand_profile: pd.DataFrame, part_route_map: pd.DataFrame,
                         tp_part_router: pd.DataFrame | None = None) -> pd.DataFrame:
    """Link each part's demand to its routes, applying route limits and normalizing rates.

    For each Part-Month-Program, any route with a non-null ``Limit`` gets that many
    units -- scaled down proportionally if the limited routes alone would add up to
    more than the part's total demand that month, so the group never allocates more
    than was actually demanded. Whatever demand is left splits across the remaining
    (unlimited) routes in proportion to their ``Rate`` values, normalized to sum to 1
    (or split evenly if those rates are all zero).

    ``Demand_Quantity`` ends up holding each route's allocated *pool* and ``Rate`` its
    *share of that pool* (always 1.0 for limited routes, since they already hold their
    exact allocated amount), so downstream ``Demand_Quantity * Rate`` always yields the
    route's real allocated quantity.

    Fully vectorized (no per-group Python callback) -- see the module docstring.
    """
    merged = demand_profile.merge(
        part_route_map[['Part', 'Router', 'Rate', 'Limit']], on='Part', how='inner',
    )
    merged['Rate'] = pd.to_numeric(merged['Rate'], errors='coerce').fillna(0.0)

    if tp_part_router is not None:
        merged = apply_timephase(
            df1=merged, df2=tp_part_router, keys1=['Part', 'Router'], keys2=['Part', 'Router'],
            date1='Month', date2='Date', val1='Limit', val2='Value', tp_param='Route Quantity',
        )
        merged = apply_timephase(
            df1=merged, df2=tp_part_router, keys1=['Part', 'Router'], keys2=['Part', 'Router'],
            date1='Month', date2='Date', val1='Rate', val2='Value', tp_param='Route Percentage',
        )
        merged['Rate'] = pd.to_numeric(merged['Rate'], errors='coerce').fillna(0.0)

    has_limit = merged['Limit'].notna()
    # Derived per-row columns first, then group ONCE and reuse that grouping for every
    # transform below -- grouping (hashing the keys) is the expensive part; a separate
    # merged.groupby(...) call per column redoes that hashing from scratch every time
    # (benchmarked: 0.38s as 4 separate calls vs 0.12s reusing one, at 288k rows).
    merged['_limit_contrib'] = merged['Limit'].where(has_limit, 0.0)
    merged['_nonlimit_rate'] = merged['Rate'].where(~has_limit, 0.0)
    merged['_nonlimit_flag'] = (~has_limit).astype(float)
    gb = merged.groupby(['Month', 'Program', 'Part'])

    total_demand = gb['Demand_Quantity'].transform('first')
    limit_total = gb['_limit_contrib'].transform('sum')

    # If the limited routes alone would exceed total demand, scale them down to fit
    # (instead of silently allocating more than was actually demanded).
    limit_scale = np.where(
        limit_total > total_demand,
        np.where(limit_total > 0, total_demand / limit_total, 1.0),
        1.0,
    )
    allocated_limit_total = np.minimum(limit_total, total_demand)
    remaining = np.clip(total_demand - allocated_limit_total, 0, None)

    nonlimit_rate_sum = gb['_nonlimit_rate'].transform('sum')
    nonlimit_count = gb['_nonlimit_flag'].transform('sum')
    norm_rate = np.where(
        remaining <= 0, 0.0,
        np.where(nonlimit_rate_sum > 0,
                 merged['Rate'] / nonlimit_rate_sum.replace(0, np.nan),
                 1.0 / nonlimit_count.replace(0, np.nan)),
    )

    merged['Rate'] = np.where(total_demand <= 0, 0.0, np.where(has_limit, 1.0, norm_rate))
    merged['Demand_Quantity'] = np.where(has_limit, merged['Limit'] * limit_scale, remaining)
    merged['Router'] = merged['Router'].astype(str)
    return merged[['Month', 'Program', 'Part', 'Demand_Quantity', 'Router', 'Rate', 'Limit']]


def join_route_information(routed_parts: pd.DataFrame, route_information: pd.DataFrame,
                            tp_router_op: pd.DataFrame | None = None) -> pd.DataFrame:
    """Expand each router into the operations/workcenters it runs through, and apply optional
    router-operation timephasing.
    """
    merged = routed_parts.merge(
        route_information[['Router', 'Operation', 'Workcenter', 'HPU', 'Sampling Rate']],
        on='Router', how='inner',
    )
    if tp_router_op is not None:
        merged = apply_timephase(
            df1=merged, df2=tp_router_op, keys1=['Router', 'Operation'], keys2=['Router', 'Operation'],
            date1='Month', date2='Date', val1='Sampling Rate', val2='Value', tp_param='Sampling Rate',
        )
        merged = apply_timephase(
            df1=merged, df2=tp_router_op, keys1=['Router', 'Operation'], keys2=['Router', 'Operation'],
            date1='Month', date2='Date', val1='HPU', val2='Value', tp_param='Processing Rate',
        )
        merged['Sampling Rate'] = pd.to_numeric(merged['Sampling Rate'], errors='coerce')
        merged['HPU'] = pd.to_numeric(merged['HPU'], errors='coerce')

    return merged[[
        'Month', 'Program', 'Part', 'Demand_Quantity', 'Router', 'Rate', 'Limit',
        'Operation', 'Workcenter', 'HPU', 'Sampling Rate',
    ]]


def join_workcenter_information(routed: pd.DataFrame, workcenter_information: pd.DataFrame,
                                 tp_workcenter: pd.DataFrame | None = None) -> pd.DataFrame:
    merged = routed.merge(
        workcenter_information[['Workcenter', 'Available Hours', 'Overtime Percentage', 'Capacity Count']],
        on='Workcenter', how='inner',
    )

    if tp_workcenter is not None:
        for val_col in ('Capacity Count', 'Available Hours', 'Overtime Percentage'):
            merged = apply_timephase(
                df1=merged, df2=tp_workcenter, keys1=['Workcenter'], keys2=['Workcenter'],
                date1='Month', date2='Date', val1=val_col, val2='Value', tp_param=val_col,
            )
        for val_col in ('Capacity Count', 'Available Hours', 'Overtime Percentage'):
            merged[val_col] = pd.to_numeric(merged[val_col], errors='coerce')

    # Scale available hours to a monthly basis using weeks per year.
    merged['Available Hours'] = merged['Available Hours'] * (_WEEKS_PER_YEAR / 12)
    # Overtime hours per week, scaled to a monthly basis the same way.
    merged['Overtime Hours'] = merged['Overtime Percentage'] * _OVERTIME_HOURS_PER_WEEK * (_WEEKS_PER_YEAR / 12)
    merged['Total Operation Hours'] = merged['Available Hours'] + merged['Overtime Hours']
    merged['Total Avail Overtime Hours'] = _OVERTIME_HOURS_PER_WEEK

    return merged[[
        'Month', 'Program', 'Part', 'Demand_Quantity', 'Router', 'Rate', 'Limit',
        'Operation', 'Workcenter', 'Available Hours', 'Overtime Percentage', 'Total Avail Overtime Hours',
        'Capacity Count', 'HPU', 'Sampling Rate', 'Overtime Hours', 'Total Operation Hours',
    ]]


def compute_required_hours(df: pd.DataFrame) -> pd.DataFrame:
    """Required_Hours = Demand_Quantity x HPU x Rate x Sampling Rate -- the two ratios chained
    together play the role the old single 'Route_Ratio' used to play alone (Rate = share of the
    part's demand that uses this router, Sampling Rate = share of that router's volume that stops
    at this operation).

    Util_Increment divides Required_Hours by (Total Operation Hours x Capacity Count) -- the
    workcenter's total monthly hour capacity across all its processing units. A workcenter with
    zero or missing capacity that period gets no demand applied: Required_Hours and
    Util_Increment are both zeroed for those rows rather than producing inf/NaN.
    """
    df = df.copy()
    df['Required_Hours'] = df['Demand_Quantity'] * df['HPU'] * df['Rate'] * df['Sampling Rate']

    capacity_hours = df['Total Operation Hours'] * df['Capacity Count']
    has_capacity = capacity_hours.notna() & (capacity_hours > 0)

    df['Util_Increment'] = 0.0
    df.loc[has_capacity, 'Util_Increment'] = df.loc[has_capacity, 'Required_Hours'] / capacity_hours[has_capacity]
    df.loc[~has_capacity, 'Required_Hours'] = 0.0
    return df


def pivot_by_workcenter_month(raw_output: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """index=Workcenter, columns=Month, values=sum(value_col)."""
    pivot = raw_output.pivot_table(
        index='Workcenter', columns='Month', values=value_col, aggfunc='sum', fill_value=0,
    )
    return pivot.sort_index(axis=1)


def workcenter_program_breakdown(raw_output: pd.DataFrame, workcenter: str, value_col: str) -> pd.DataFrame:
    """index=Month, columns=Program, values=sum(value_col), for one workcenter. Feeds the stacked chart."""
    subset = raw_output[raw_output['Workcenter'] == workcenter]
    pivot = subset.pivot_table(index='Month', columns='Program', values=value_col, aggfunc='sum', fill_value=0)
    return pivot.sort_index()


def run_capacity_model(sheets: dict[str, pd.DataFrame]) -> dict:
    """Run the full pipeline. `sheets` is the dict returned by excel_io.load_workbook."""
    _validate_workbook(sheets)

    monthly_demand = unpivot_monthly_demand(sheets['Program Monthly Demand'])
    demand_profile = build_demand_profile(monthly_demand, sheets['BOM'], sheets['Annual Max Limits'])
    routed_parts = join_part_route_map(
        demand_profile, sheets['Part Route Map'], tp_part_router=sheets.get('TP Part Router'))
    routed = join_route_information(
        routed_parts, sheets['Route Information'], tp_router_op=sheets.get('TP Router Operation'))
    with_workcenter = join_workcenter_information(
        routed, sheets['Workcenter Information'], tp_workcenter=sheets.get('TP Workcenter'))

    capacity_hours = with_workcenter['Total Operation Hours'] * with_workcenter['Capacity Count']
    zero_capacity_workcenters = sorted(
        with_workcenter.loc[capacity_hours.isna() | (capacity_hours <= 0), 'Workcenter'].unique().tolist()
    )

    raw_output = compute_required_hours(with_workcenter)
    column_order = [
        'Month', 'Program', 'Part', 'Demand_Quantity', 'Router', 'Rate', 'Limit',
        'Operation', 'Sampling Rate', 'Workcenter', 'HPU', 'Available Hours',
        'Overtime Percentage', 'Total Avail Overtime Hours', 'Overtime Hours',
        'Total Operation Hours', 'Capacity Count', 'Required_Hours', 'Util_Increment',
    ]
    raw_output = raw_output[column_order]

    available_hours_by_workcenter = (
        sheets['Workcenter Information'].set_index('Workcenter')['Available Hours'].to_dict()
    )

    return {
        'raw_output': raw_output,
        'util_pivot': pivot_by_workcenter_month(raw_output, 'Util_Increment'),
        'hours_pivot': pivot_by_workcenter_month(raw_output, 'Required_Hours'),
        'available_hours_by_workcenter': available_hours_by_workcenter,
        'zero_capacity_workcenters': zero_capacity_workcenters,
    }
