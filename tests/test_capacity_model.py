"""Regression test for the capacity model pipeline.

Locks in the known-good output for tests/fixtures/example_workbook.xlsx (a
frozen snapshot, independent of data/ExampleExcelFile.xlsx so this test
stays stable while that file keeps changing during active development).

This is a characterization test: it asserts the pipeline keeps producing
the values that were confirmed correct on 2026-10-08, not that those values
are necessarily bug-free forever. If a deliberate fix changes the numbers
(e.g. the known MakePercent normalization issue), regenerate the fixtures
with scripts/regenerate_capacity_model_fixtures.py and note why in the
commit that updates them -- don't "fix" a failure here without checking
whether the change was intentional.
"""
from pathlib import Path

import pandas as pd
import pytest

from hub.tools.capacity_model import run_capacity_model
from hub.tools.excel_io import load_workbook

FIXTURES_DIR = Path(__file__).resolve().parent / 'fixtures'
WORKBOOK = FIXTURES_DIR / 'example_workbook.xlsx'

KEY_COLUMNS = ['Month', 'Program', 'Part', 'Router', 'Operation', 'Workcenter']
VALUE_COLUMNS = [
    'Demand_Quantity', 'Rate', 'Limit', 'Sampling Rate', 'HPU', 'Available Hours',
    'Overtime Hours', 'Total Operation Hours', 'Capacity Count', 'Required_Hours', 'Util_Increment',
]


@pytest.fixture(scope='module')
def result() -> dict:
    sheets = load_workbook(WORKBOOK)
    return run_capacity_model(sheets)


def test_raw_output_matches_known_good_values(result: dict) -> None:
    actual = result['raw_output'][KEY_COLUMNS + VALUE_COLUMNS].sort_values(KEY_COLUMNS).reset_index(drop=True)
    expected = pd.read_csv(FIXTURES_DIR / 'expected_raw_output.csv', parse_dates=['Month'])
    pd.testing.assert_frame_equal(actual, expected, check_exact=False, rtol=1e-6, atol=1e-9)


def test_required_hours_and_util_increment_totals(result: dict) -> None:
    """Extra emphasis on the two main outputs: check their workcenter/month pivots directly."""
    actual_util = result['util_pivot']
    expected_util = pd.read_csv(FIXTURES_DIR / 'expected_util_pivot.csv', index_col='Workcenter')
    expected_util.columns = pd.to_datetime(expected_util.columns)
    pd.testing.assert_frame_equal(actual_util, expected_util, check_exact=False, rtol=1e-6, atol=1e-9,
                                   check_names=False)

    actual_hours = result['hours_pivot']
    expected_hours = pd.read_csv(FIXTURES_DIR / 'expected_hours_pivot.csv', index_col='Workcenter')
    expected_hours.columns = pd.to_datetime(expected_hours.columns)
    pd.testing.assert_frame_equal(actual_hours, expected_hours, check_exact=False, rtol=1e-6, atol=1e-9,
                                   check_names=False)
