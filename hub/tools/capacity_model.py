"""Manufacturing capacity model.

Pipeline: monthly program demand (wide) -> unpivot -> explode through the
BOM (quantity-per-unit x make %) -> join routing (hours-per-unit x route
ratio) -> join workcenter available hours -> required hours & utilization.

Pure business logic, no UI imports, so it can be run/tested independently
of the hub.

Assumption worth flagging: "Demand_Quantity" is computed as
ceil(monthly Demand x BOM Quantity x MakePercent) -- i.e. it scales with
the month's program demand, not just the BOM ratio on its own (a value that
didn't vary by month wouldn't fit the resulting [Month, Program, Part, ...]
shape). Flag if that's not the intended formula.
"""
import math

import pandas as pd

REQUIRED_SHEETS = ('Program Monthly Demand', 'BOM', 'Route Information', 'Workcenter Information')


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


def build_demand_profile(monthly_demand: pd.DataFrame, bom: pd.DataFrame) -> pd.DataFrame:
    """Explode program demand into per-part demand via the BOM (quantity-per-unit x make %)."""
    merged = monthly_demand.merge(bom, on='Program', how='inner')
    merged['Demand_Quantity'] = (
        merged['Demand'] * merged['Quantity'] * merged['MakePercent']
    ).apply(math.ceil)
    return merged[['Month', 'Program', 'Part', 'Demand_Quantity']]


def join_route_information(demand_profile: pd.DataFrame, route_information: pd.DataFrame) -> pd.DataFrame:
    merged = demand_profile.merge(route_information, on='Part', how='inner')
    return merged[[
        'Month', 'Program', 'Part', 'Demand_Quantity',
        'Tasklist_Num', 'Operation', 'Workcenter', 'HPU', 'Route_Ratio',
    ]]


def join_workcenter_information(routed: pd.DataFrame, workcenter_information: pd.DataFrame) -> pd.DataFrame:
    merged = routed.merge(workcenter_information, on='Workcenter', how='inner')
    return merged[[
        'Month', 'Program', 'Part', 'Demand_Quantity',
        'Tasklist_Num', 'Operation', 'Workcenter', 'Available_Hours', 'HPU', 'Route_Ratio',
    ]]


def compute_required_hours(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['Required_Hours'] = df['Demand_Quantity'] * df['HPU'] * df['Route_Ratio']
    df['Util_Increment'] = df['Required_Hours'] / df['Available_Hours']
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
    demand_profile = build_demand_profile(monthly_demand, sheets['BOM'])
    routed = join_route_information(demand_profile, sheets['Route Information'])
    with_workcenter = join_workcenter_information(routed, sheets['Workcenter Information'])
    raw_output = compute_required_hours(with_workcenter)

    available_hours_by_workcenter = (
        sheets['Workcenter Information'].set_index('Workcenter')['Available_Hours'].to_dict()
    )

    return {
        'raw_output': raw_output,
        'util_pivot': pivot_by_workcenter_month(raw_output, 'Util_Increment'),
        'hours_pivot': pivot_by_workcenter_month(raw_output, 'Required_Hours'),
        'available_hours_by_workcenter': available_hours_by_workcenter,
    }
