"""Manufacturing capacity model.

Pipeline: monthly program demand (wide) -> unpivot -> explode through the
BOM (quantity-per-unit x make %) -> join routing (hours-per-unit x route
ratio) -> join workcenter available hours -> required hours & utilization.

Pure business logic, no UI imports, so it can be run/tested independently
of the hub.

Assumption worth flagging: "Demand_Quantity" is computed as
monthly Demand x BOM Quantity x MakePercent -- i.e. it scales with the
month's program demand, not just the BOM ratio on its own (a value that
didn't vary by month wouldn't fit the resulting [Month, Program, Part, ...]
shape). Flag if that's not the intended formula.

Second assumption: the Annual Max Limit cap groups by calendar year (not a
rolling 12 months or a fiscal year starting elsewhere). Final rounding uses
ceil() for consistency with the original "take the ceiling" instruction --
note this can push a capped part-year's total slightly back above its Max
(ceiling several scaled-down months each rounds up), so flag if floor/round
is actually wanted there instead.
"""
import math

import pandas as pd
from hub.tools.timephase_function import apply_timephase

# ----- Timephasing helper -----
# The project provides a reusable function ``apply_timephase`` in
# ``timephase_function.py`` that overrides values in a DataFrame based on the
# most recent matching record from another DataFrame.  We embed a copy here so the


import numpy as np

# ----- Configuration -----
# Default number of weeks per year used to scale workcenter available hours.
# This can be overridden by the front‑end via `set_weeks_per_year`.
_WEEKS_PER_YEAR = 50

def set_weeks_per_year(weeks: int) -> None:
    """Override the weeks‑per‑year factor used in `join_workcenter_information`.

    The front‑end should call this once on startup or when the user updates the setting.
    """
    global _WEEKS_PER_YEAR
    _WEEKS_PER_YEAR = weeks

# Overtime hours per week configuration (default 24)
_OVERTIME_HOURS_PER_WEEK = 24

def set_overtime_hours_per_week(hours: int) -> None:
    """Override the default overtime hours per week.

    The front‑end should call this when the user changes the overtime input.
    """
    global _OVERTIME_HOURS_PER_WEEK
    _OVERTIME_HOURS_PER_WEEK = hours


REQUIRED_SHEETS = (
    'Program Monthly Demand', 'BOM', 'Annual Max Limits',
    'Part Route Map', 'Route Information', 'Workcenter Information',
)


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
    # Normalize MakePercent per Part so they sum to 1 (or evenly split if sum is zero)
    bom = bom.copy()
    bom['MakePercent'] = pd.to_numeric(bom['MakePercent'], errors='coerce').fillna(0.0)
    sum_per_part = bom.groupby('Part')['MakePercent'].transform('sum')
    # Identify parts where the sum is zero
    zero_sum = sum_per_part == 0
    # Count viable rows per Part
    count_per_part = bom.groupby('Part')['MakePercent'].transform('size')
    # For zero-sum parts, assign equal share
    bom.loc[zero_sum, 'MakePercent'] = 1.0 / count_per_part[zero_sum]
    # For non-zero-sum parts, normalize to sum to 1
    bom.loc[~zero_sum, 'MakePercent'] = bom.loc[~zero_sum, 'MakePercent'] / sum_per_part[~zero_sum]

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
    # Scale raw quantity by ratio
    scaled = merged['Raw_Quantity'] * merged['Ratio']
    # Initial integer demand using floor to avoid overshoot
    merged['Demand_Quantity'] = np.floor(scaled).astype(int)

    # Enforce annual caps without exceeding the Max limit
    # Identify parts that are capped (Max defined and less than actual total)
    capped_parts = annual[annual['Max'].notna() & (annual['Max'] < annual['Actual'])]
    for _, cap in capped_parts.iterrows():
        part = cap['Part']
        year = cap['Year']
        max_qty = int(cap['Max'])
        mask = (merged['Part'] == part) & (merged['Year'] == year)
        current = merged.loc[mask, 'Demand_Quantity'].sum()
        remainder = max_qty - current
        if remainder > 0:
            # Distribute remaining units to rows with largest fractional remainders
            fractions = scaled[mask] - np.floor(scaled[mask])
            # Get indices of rows sorted by descending fraction
            idx = fractions.sort_values(ascending=False).index[:remainder]
            merged.loc[idx, 'Demand_Quantity'] += 1
    return merged[['Month', 'Program', 'Part', 'Demand_Quantity']]



def join_part_route_map(demand_profile: pd.DataFrame, part_route_map: pd.DataFrame, tp_part_router: pd.DataFrame | None = None) -> pd.DataFrame:
    """Link each part's demand to its routes, applying route limits and normalizing rates.

    For each Part‑Month, any route with a non‑null ``Limit`` receives that many units
    up front. The remaining demand is then split across the other routes in proportion
    to their ``Rate`` values (normalized to sum to 1). The resulting ``Rate`` column
    represents the *fraction of the total part demand* that should be assigned to
    the route, so downstream ``Demand_Quantity * Rate`` yields the correct allocated
    quantity.
    """
    # Merge demand profile (per part‑month) with route map (multiple rows per part)
    merged = demand_profile.merge(
        part_route_map[['Part', 'Router', 'Rate', 'Limit']], on='Part', how='inner',
    )

    # Ensure numeric types; treat missing limits as 0
    merged['Rate'] = pd.to_numeric(merged['Rate'], errors='coerce').fillna(0.0)
    # merged['Limit'] = pd.to_numeric(merged['Limit'], errors='coerce').fillna(0.0)
    # Apply timephasing if TP data is supplied
    if tp_part_router is not None:
        # Apply Route Quantity to Limit
        merged = apply_timephase(
            df1=merged,
            df2=tp_part_router,
            keys1=['Part', 'Router'],
            keys2=['Part', 'Router'],
            date1='Month',
            date2='Date',
            val1='Limit',
            val2='Value',
            tp_param='Route Quantity',
        )
        # Apply Route Percentage to Rate
        merged = apply_timephase(
            df1=merged,
            df2=tp_part_router,
            keys1=['Part', 'Router'],
            keys2=['Part', 'Router'],
            date1='Month',
            date2='Date',
            val1='Rate',
            val2='Value',
            tp_param='Route Percentage',
        )
        # Re‑ensure numeric after timephasing
        merged['Rate'] = pd.to_numeric(merged['Rate'], errors='coerce').fillna(0.0)
        # merged['Limit'] = pd.to_numeric(merged['Limit'], errors='coerce').fillna(0.0)

    # Allocate per Part‑Month group
    def alloc_group(df):
        # Preserve original demand values (may differ per row after BOM expansion)
        # Sum the original demand across distinct programs (avoid duplication from router join)
        # Use the demand value from the first row (all rows share the same original demand within this group)
        total_demand = df['Demand_Quantity'].iloc[0]
        if total_demand == 0:
            df['Rate'] = 0.0
            return df
        # ------------------------------------------------
        # 1️⃣ Handle routes that have a Limit
        limit_mask = df['Limit'].notna()
        # Sum each limit only once per router (limits apply to the part‑month, not per program)
        limit_total = df.loc[limit_mask].drop_duplicates(subset=['Router'])['Limit'].sum()
        if limit_mask.any():
            # For each limited router, allocate the full limit amount (Rate = 1.0)
            for router in df.loc[limit_mask, 'Router'].unique():
                mask = limit_mask & (df['Router'] == router)
                df.loc[mask, 'Demand_Quantity'] = df.loc[mask, 'Limit']
                df.loc[mask, 'Rate'] = 1.0
        # ------------------------------------------------
        # 2️⃣ Distribute the remaining demand to routes without a limit
        remaining = total_demand - limit_total
        nonlimit_mask = ~limit_mask
        if remaining > 0 and nonlimit_mask.any():
            # Original rates for the non‑limit rows
            orig_rates = df.loc[nonlimit_mask, 'Rate']
            rate_sum = orig_rates.sum()
            # Normalise rates so they sum to 1 across the non‑limit routes
            if rate_sum > 0:
                norm_rates = orig_rates / rate_sum
            else:
                # If all original rates are zero, split evenly
                norm_rates = pd.Series(1.0 / nonlimit_mask.sum(), index=orig_rates.index)
            # Assign normalized rates
            df.loc[nonlimit_mask, 'Rate'] = norm_rates
            # Allocate demand proportionally to those rates
            df.loc[nonlimit_mask, 'Demand_Quantity'] = int(remaining)
        else:
            # No remaining demand – set non‑limit rows to zero
            df.loc[nonlimit_mask, 'Rate'] = 0.0
            df.loc[nonlimit_mask, 'Demand_Quantity'] = 0
        return df

    merged = merged.groupby(['Month', 'Program', 'Part']).apply(alloc_group).reset_index()
    merged['Router'] = merged['Router'].astype(str)
    return merged[['Month', 'Program', 'Part', 'Demand_Quantity', 'Router', 'Rate', 'Limit']]


def join_route_information(routed_parts: pd.DataFrame, route_information: pd.DataFrame, tp_router_op: pd.DataFrame | None = None) -> pd.DataFrame:
    """Expand each router into the operations/workcenters it runs through, and apply optional router‑operation timephasing."""
    merged = routed_parts.merge(
        route_information[['Router', 'Operation', 'Workcenter', 'HPU', 'Sampling Rate']],
        on='Router', how='inner',
    )
    # Apply optional timephasing to HPU based on router‑operation TP data
    if tp_router_op is not None:
        # Apply Sampling Rate TP (overrides Sampling Rate column)
        merged = apply_timephase(
            df1=merged,
            df2=tp_router_op,
            keys1=['Router', 'Operation'],
            keys2=['Router', 'Operation'],
            date1='Month',
            date2='Date',
            val1='Sampling Rate',
            val2='Value',
            tp_param='Sampling Rate',
        )
        # Apply Processing Rate TP (overrides HPU column)
        merged = apply_timephase(
            df1=merged,
            df2=tp_router_op,
            keys1=['Router', 'Operation'],
            keys2=['Router', 'Operation'],
            date1='Month',
            date2='Date',
            val1='HPU',
            val2='Value',
            tp_param='Processing Rate',
        )
        # Ensure numeric types for the overridden columns
        merged['Sampling Rate'] = pd.to_numeric(merged['Sampling Rate'], errors='coerce')
        merged['HPU'] = pd.to_numeric(merged['HPU'], errors='coerce')
        return merged[[
            'Month', 'Program', 'Part', 'Demand_Quantity', 'Router', 'Rate', 'Limit',
            'Operation', 'Workcenter', 'HPU', 'Sampling Rate',
        ]]


def join_workcenter_information(routed: pd.DataFrame, workcenter_information: pd.DataFrame, tp_workcenter: pd.DataFrame | None = None) -> pd.DataFrame:
    merged = routed.merge(
        workcenter_information[['Workcenter', 'Available Hours', 'Overtime Percentage', 'Capacity Count']],
        on='Workcenter', how='inner',
    )

    # Apply optional workcenter timephasing
    if tp_workcenter is not None:
        # Capacity Count TP
        merged = apply_timephase(
            df1=merged,
            df2=tp_workcenter,
            keys1=['Workcenter'],
            keys2=['Workcenter'],
            date1='Month',
            date2='Date',
            val1='Capacity Count',
            val2='Value',
            tp_param='Capacity Count',
        )
        # Available Hours TP
        merged = apply_timephase(
            df1=merged,
            df2=tp_workcenter,
            keys1=['Workcenter'],
            keys2=['Workcenter'],
            date1='Month',
            date2='Date',
            val1='Available Hours',
            val2='Value',
            tp_param='Available Hours',
        )
        # Overtime Percentage TP
        merged = apply_timephase(
            df1=merged,
            df2=tp_workcenter,
            keys1=['Workcenter'],
            keys2=['Workcenter'],
            date1='Month',
            date2='Date',
            val1='Overtime Percentage',
            val2='Value',
            tp_param='Overtime Percentage',
        )
        # Ensure numeric types for overridden columns
        merged['Capacity Count'] = pd.to_numeric(merged['Capacity Count'], errors='coerce')
        merged['Available Hours'] = pd.to_numeric(merged['Available Hours'], errors='coerce')
        merged['Overtime Percentage'] = pd.to_numeric(merged['Overtime Percentage'], errors='coerce')
        
    # Scale available hours to a monthly basis using weeks per year (same as before)
    merged['Available Hours'] = merged['Available Hours'] * (_WEEKS_PER_YEAR / 12)
    # Compute overtime hours per week for each workcenter using the overtime percentage
    merged['Overtime Hours'] = merged['Overtime Percentage'] * _OVERTIME_HOURS_PER_WEEK
    # Convert overtime hours to a monthly value using weeks per year (now /12 as per updated spec)
    merged['Overtime Hours'] = merged['Overtime Hours'] * (_WEEKS_PER_YEAR / 12)
    # Total operation hours = regular available + overtime hours
    merged['Total Operation Hours'] = merged['Available Hours'] + merged['Overtime Hours']
    # Add column showing the configured overtime hours per week
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
    at this operation). Util_Increment divides straight by Available Hours; Overtime Percentage
    and Capacity Count aren't factored in yet, pending how they should apply.
    """
    df = df.copy()
    df['Required_Hours'] = df['Demand_Quantity'] * df['HPU'] * df['Rate'] * df['Sampling Rate']
    # Use total operation hours (available + overtime) for utilization calculation
    df['Util_Increment'] = df['Required_Hours'] / (df['Total Operation Hours'] * df['Capacity Count'])
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
    missing = [name for name in REQUIRED_SHEETS if name not in sheets]
    if missing:
        raise ValueError(f"Workbook is missing required sheet(s): {', '.join(missing)}")

    monthly_demand = unpivot_monthly_demand(sheets['Program Monthly Demand'])
    demand_profile = build_demand_profile(monthly_demand, sheets['BOM'], sheets['Annual Max Limits'])
    routed_parts = join_part_route_map(demand_profile, sheets['Part Route Map'], tp_part_router=sheets.get('TP Part Router'))
    routed = join_route_information(routed_parts, sheets['Route Information'], tp_router_op=sheets.get('TP Router Operation'))
    with_workcenter = join_workcenter_information(routed, sheets['Workcenter Information'], tp_workcenter=sheets.get('TP Workcenter'))
    raw_output = compute_required_hours(with_workcenter)
    # Reorder columns to the requested layout
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
    }
