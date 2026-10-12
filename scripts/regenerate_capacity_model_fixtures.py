"""Regenerate tests/fixtures/expected_*.csv from tests/fixtures/example_workbook.xlsx.

Only run this when you've made a deliberate change to the pipeline and
confirmed the new numbers are correct -- this overwrites the "known good"
values that test_capacity_model.py checks against. Mention why in the
commit that updates the fixtures.
"""
from pathlib import Path

from hub.tools.capacity_model import run_capacity_model
from hub.tools.excel_io import load_workbook

FIXTURES_DIR = Path(__file__).resolve().parent.parent / 'tests' / 'fixtures'
WORKBOOK = FIXTURES_DIR / 'example_workbook.xlsx'

KEY_COLUMNS = ['Month', 'Program', 'Part', 'Router', 'Operation', 'Workcenter']
VALUE_COLUMNS = [
    'Demand_Quantity', 'Rate', 'Limit', 'Sampling Rate', 'HPU', 'Available Hours',
    'Overtime Hours', 'Total Operation Hours', 'Capacity Count', 'Required_Hours', 'Util_Increment',
]


def main() -> None:
    sheets = load_workbook(WORKBOOK)
    result = run_capacity_model(sheets)

    raw = result['raw_output'][KEY_COLUMNS + VALUE_COLUMNS].sort_values(KEY_COLUMNS).reset_index(drop=True)
    raw.to_csv(FIXTURES_DIR / 'expected_raw_output.csv', index=False)
    result['util_pivot'].to_csv(FIXTURES_DIR / 'expected_util_pivot.csv')
    result['hours_pivot'].to_csv(FIXTURES_DIR / 'expected_hours_pivot.csv')
    print(f'Wrote fixtures to {FIXTURES_DIR}')


if __name__ == '__main__':
    main()
