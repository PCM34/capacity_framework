import pandas as pd
from pathlib import Path
print('******', Path.cwd())
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[2]))
import hub.tools.database_utils as dbu
import numpy as np


def connect_db():
    db_configs = pd.read_json("hub/tools/db_connections.json")
    con = dbu.create_con(db_configs, "HAP")
    return con


def get_router_data(sql_path, excel_df, HAP):
    all_routes_df = dbu.basic_query(sql_path + "route.sql", HAP)
    print(all_routes_df.columns)
    material_set = excel_df["BOM"]["Part"].drop_duplicates().to_list()

    all_routes_df = all_routes_df[all_routes_df["MATERIAL"].isin(material_set)]
    
    all_routes_df = all_routes_df[
        ["ROUTING_GROUP", "MATERIAL", "OPERATION", "WORK_CENTER"]
    ]
    all_routes_df.columns = ["Router", "Part", "Operation", "Workcenter"]

    part_router_map_df = all_routes_df[["Part", "Router"]].drop_duplicates()
    router_information_df = all_routes_df[
        ["Router", "Operation", "Workcenter"]
    ].drop_duplicates()
    hpu_needs_df = all_routes_df[["Part", "Operation", "Workcenter"]].drop_duplicates()

    return part_router_map_df, router_information_df, hpu_needs_df

def prep_part_router_map(sql_path, HAP, part_router_map_df):
    route_demand_df = dbu.basic_query(sql_path + 'route_demand_raw.sql', HAP)
    route_demand_df.columns = ['Part', 'Router', 'QTY']
    part_router_map_df = pd.merge(part_router_map_df, route_demand_df, how='left', on=['Part', 'Router'])

    part_router_map_df['QTY'] = part_router_map_df['QTY'].fillna(0)
    part_router_map_df['TotalQty'] = part_router_map_df.groupby('Part')['QTY'].transform('sum')
    part_router_map_df['NumRouters'] = part_router_map_df.groupby('Part')['Router'].transform('nunique')

    def get_rate(row):
        qty = row['QTY']
        total_qty = row['TotalQty']
        num_routers = row['NumRouters']

        if total_qty > 0:
            return qty / total_qty

        return 1 / num_routers

    part_router_map_df['Rate'] = part_router_map_df.apply(get_rate, axis=1)

    part_router_map_df = part_router_map_df[['Part', 'Router', 'Rate']]
    part_router_map_df['Limit'] = np.nan
    part_router_map_df['Creator'] = 'System'

    return part_router_map_df

def prep_router_info(sql_path, HAP, hpu_needs_df, part_router_map_df, router_information_df):
    hpu_df = dbu.basic_query(sql_path + 'hpu.sql', HAP)
    hpu_df.columns = ['Workcenter', 'WipOrder', 'Part', 'Operation', 'HPU']
    hpu_df = hpu_df[(hpu_df['Part'].notna()) & (hpu_df['Part'] != '')]

    levels = [['Part', 'Operation', 'Workcenter'], ['Part', 'Workcenter'], ['Operation', 'Workcenter'], ['Workcenter']]
    results = []
    for level in levels:
        if len(hpu_needs_df) == 0:
            break
        current_hpu_data_df = hpu_df.groupby(level).agg(HPU=('HPU','median'), records=('WipOrder', 'count')).reset_index()
        current_hpu_data_df = current_hpu_data_df[current_hpu_data_df['records'] > 15]
        current_hpu_data_df = current_hpu_data_df.drop(columns='records')
        hpu_needs_df = pd.merge(hpu_needs_df, current_hpu_data_df, how='left', on=level)
        results.append(hpu_needs_df[hpu_needs_df['HPU'].notna()])
        hpu_needs_df = hpu_needs_df[hpu_needs_df['HPU'].isna()]
        hpu_needs_df = hpu_needs_df.drop(columns='HPU')
    if len(hpu_needs_df) > 0:
        hpu_needs_df['HPU'] = 0
        results.append(hpu_needs_df)
    hpu_needs_df = pd.concat(results)

    hpu_transer_df = pd.merge(hpu_needs_df, part_router_map_df[['Part', 'Router']], how='inner', on='Part')
    hpu_transer_df = hpu_transer_df[['Router', 'Operation', 'Workcenter', 'HPU']].drop_duplicates()
    router_information_df = pd.merge(router_information_df, hpu_transer_df, how='left', on=['Router', 'Operation', 'Workcenter'])
    router_information_df['HPU'] = router_information_df['HPU'].fillna(0)
    router_information_df['Sampling Rate']= 1.0
    router_information_df['Creator'] = 'System'

    return router_information_df


def update_data():
    EXCEL_FILE = "data/ExampleExcelFile.xlsx"
    HAP = connect_db()
    excel_df = pd.read_excel(EXCEL_FILE, sheet_name=None)
    print(94)
    sql_path = "hub/sql/"
    print(96)
    part_router_map_df, router_information_df, hpu_needs_df = get_router_data(
        sql_path, excel_df, HAP
    )
    print(100)
    part_router_map_df = prep_part_router_map(sql_path, HAP, part_router_map_df)
    print(101)
    router_information_df = prep_router_info(sql_path, HAP, hpu_needs_df, part_router_map_df, router_information_df)
    print(102)
    with pd.ExcelWriter(EXCEL_FILE, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer: part_router_map_df.to_excel(writer, sheet_name='Part Route Map', index=False)
    with pd.ExcelWriter(EXCEL_FILE, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer: router_information_df.to_excel(writer, sheet_name='Route Information', index=False)
    print('done')

update_data()