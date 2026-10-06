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
    merged['Demand_Quantity'] = (merged['Raw_Quantity'] * merged['Ratio']).apply(math.ceil)
    return merged[['Month', 'Program', 'Part', 'Demand_Quantity']]


def join_part_route_map(demand_profile: pd.DataFrame, part_route_map: pd.DataFrame) -> pd.DataFrame:
    """Link each part's demand to the router(s) it travels through.

    Deliberately selects only [Part, Router, Rate, Limit] -- Part Route Map
    also has a 'Creator' column that collides with Route Information's own
    'Creator' column; neither is used in the model, so both get dropped here
    rather than merged in and suffixed.
    """
    merged = demand_profile.merge(
        part_route_map[['Part', 'Router', 'Rate', 'Limit']], on='Part', how='inner',
    )
    return merged[['Month', 'Program', 'Part', 'Demand_Quantity', 'Router', 'Rate', 'Limit']]


def join_route_information(routed_parts: pd.DataFrame, route_information: pd.DataFrame) -> pd.DataFrame:
    """Expand each router into the operations/workcenters it runs through."""
    merged = routed_parts.merge(
        route_information[['Router', 'Operation', 'Workcenter', 'HPU', 'Sampling Rate']],
        on='Router', how='inner',
    )
    return merged[[
        'Month', 'Program', 'Part', 'Demand_Quantity', 'Router', 'Rate', 'Limit',
        'Operation', 'Workcenter', 'HPU', 'Sampling Rate',
    ]]


def join_workcenter_information(routed: pd.DataFrame, workcenter_information: pd.DataFrame) -> pd.DataFrame:
    merged = routed.merge(
        workcenter_information[['Workcenter', 'Available Hours', 'Overtime Percentage', 'Capacity Count']],
        on='Workcenter', how='inner',
    )
    return merged[[
        'Month', 'Program', 'Part', 'Demand_Quantity', 'Router', 'Rate', 'Limit',
        'Operation', 'Workcenter', 'Available Hours', 'Overtime Percentage', 'Capacity Count',
        'HPU', 'Sampling Rate',
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
    df['Util_Increment'] = df['Required_Hours'] / df['Available Hours']
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
    routed_parts = join_part_route_map(demand_profile, sheets['Part Route Map'])
    routed = join_route_information(routed_parts, sheets['Route Information'])
    with_workcenter = join_workcenter_information(routed, sheets['Workcenter Information'])
    raw_output = compute_required_hours(with_workcenter)

    available_hours_by_workcenter = (
        sheets['Workcenter Information'].set_index('Workcenter')['Available Hours'].to_dict()
    )

    return {
        'raw_output': raw_output,
        'util_pivot': pivot_by_workcenter_month(raw_output, 'Util_Increment'),
        'hours_pivot': pivot_by_workcenter_month(raw_output, 'Required_Hours'),
        'available_hours_by_workcenter': available_hours_by_workcenter,
    }
