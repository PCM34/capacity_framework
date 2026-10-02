"""Demo capacity model: required headcount via Erlang C.

Pure business logic, no UI imports. Keep your real capacity models in this
same style (plain functions in/out) so they stay runnable and testable on
their own, independent of the hub.
"""
import math
from dataclasses import dataclass

import pandas as pd


@dataclass
class CapacityInputs:
    volume_per_day: float       # contacts/transactions per day
    aht_seconds: float          # average handle time, seconds
    shrinkage_pct: float        # 0-100, non-productive time (breaks, training, etc.)
    target_service_level_pct: float  # 0-100, e.g. 80 for an 80/20 SLA
    target_answer_time_sec: float    # acceptable wait time, seconds
    hours_per_agent_per_day: float = 8.0


def _pwait(agents: int, traffic_erlangs: float) -> float:
    """Erlang C probability of wait, standard formula."""
    sum_terms = sum((traffic_erlangs ** n) / math.factorial(n) for n in range(agents))
    last_term = (traffic_erlangs ** agents) / math.factorial(agents)
    numerator = last_term * (agents / (agents - traffic_erlangs))
    denominator = sum_terms + numerator
    return numerator / denominator


def service_level(agents: int, traffic_erlangs: float, aht_seconds: float, target_answer_time_sec: float) -> float:
    """Fraction of contacts answered within target_answer_time_sec."""
    if agents <= traffic_erlangs:
        return 0.0
    pwait = _pwait(agents, traffic_erlangs)
    exponent = -(agents - traffic_erlangs) * (target_answer_time_sec / aht_seconds)
    return 1 - pwait * math.exp(exponent)


def required_agents(inputs: CapacityInputs) -> int:
    """Smallest agent count meeting the target service level."""
    calls_per_second = inputs.volume_per_day / (inputs.hours_per_agent_per_day * 3600)
    traffic_erlangs = calls_per_second * inputs.aht_seconds

    agents = max(1, math.ceil(traffic_erlangs))
    target = inputs.target_service_level_pct / 100
    while service_level(agents, traffic_erlangs, inputs.aht_seconds, inputs.target_answer_time_sec) < target:
        agents += 1
        if agents > traffic_erlangs + 500:  # safety valve against pathological inputs
            break
    return agents


def run_capacity_model(inputs: CapacityInputs) -> dict:
    """Full result: required FTE plus a volume-sensitivity table for charting."""
    base_agents = required_agents(inputs)
    shrinkage_factor = 1 - (inputs.shrinkage_pct / 100)
    required_fte = base_agents / shrinkage_factor if shrinkage_factor > 0 else float("inf")

    rows = []
    for multiplier in (0.7, 0.85, 1.0, 1.15, 1.3):
        scenario_inputs = CapacityInputs(
            volume_per_day=inputs.volume_per_day * multiplier,
            aht_seconds=inputs.aht_seconds,
            shrinkage_pct=inputs.shrinkage_pct,
            target_service_level_pct=inputs.target_service_level_pct,
            target_answer_time_sec=inputs.target_answer_time_sec,
            hours_per_agent_per_day=inputs.hours_per_agent_per_day,
        )
        scenario_agents = required_agents(scenario_inputs)
        scenario_fte = scenario_agents / shrinkage_factor if shrinkage_factor > 0 else float("inf")
        rows.append({
            "volume_per_day": round(scenario_inputs.volume_per_day),
            "required_agents": scenario_agents,
            "required_fte": round(scenario_fte, 1),
        })

    return {
        "required_agents": base_agents,
        "required_fte": round(required_fte, 1),
        "sensitivity_table": pd.DataFrame(rows),
    }
