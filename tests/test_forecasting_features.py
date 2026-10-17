import json
import math

import pytest

from sentinel.forecasting.features import (
    GROUP_BASE,
    GROUP_CALENDAR,
    GROUP_FLOW,
    GROUP_LEVEL,
    GROUP_SENSORS,
    IdTranslator,
    SensorLayout,
    canonical_id,
    group_contributions,
    gru_calendar,
    gru_feature_names,
    gru_step_features,
    hour_of_day,
    xgb_feature_names,
    xgb_features,
)
from sentinel.forecasting.xgb import portable_booster_json
from tests.forecasting_helpers import LAYOUT, T0, request, short_ids, window


def test_layout_round_trip() -> None:
    assert SensorLayout.from_dict(LAYOUT.to_dict()) == LAYOUT


def test_xgb_names_exclude_the_target_sensor() -> None:
    names = xgb_feature_names(LAYOUT, "Pressure_n4")
    assert "pressure:Pressure_n4" not in names
    assert names[:4] == ["flow:Flow_p227", "flow:Flow_p235", "flow:Flow_PUMP_1", "level:Level_T1"]
    assert names[4:6] == ["cal:hour", "cal:day_of_week"]
    assert names[6:] == ["pressure:Pressure_n1", "pressure:Pressure_n31"]


def test_xgb_names_reject_non_pressure_target() -> None:
    with pytest.raises(KeyError):
        xgb_feature_names(LAYOUT, "Flow_p227")


def test_xgb_features_follow_the_names() -> None:
    row = xgb_features(window(T0), LAYOUT, "Pressure_n4")
    assert len(row) == len(xgb_feature_names(LAYOUT, "Pressure_n4"))
    assert row[:4] == [20.0, 21.0, 22.0, 3.0]
    assert row[4] == pytest.approx(hour_of_day(T0)) == pytest.approx(6 + 35 / 60)
    assert row[5] == 3.0  # Thursday
    assert row[6:] == [50.0, 52.0]  # n1 and n31, never n4


def test_group_contributions_add_up() -> None:
    names = xgb_feature_names(LAYOUT, "Pressure_n1")
    values = [0.5, 0.25, 0.0, 0.4, -0.3, 0.1, 0.2, 0.15]
    grouped = group_contributions(names, values, bias=60.0)
    assert set(grouped) == {GROUP_BASE, GROUP_FLOW, GROUP_LEVEL, GROUP_CALENDAR, GROUP_SENSORS}
    assert grouped[GROUP_FLOW] == pytest.approx(0.75)
    assert grouped[GROUP_CALENDAR] == pytest.approx(-0.2)
    assert sum(grouped.values()) == pytest.approx(60.0 + sum(values))


def test_group_contributions_length_mismatch() -> None:
    with pytest.raises(ValueError):
        group_contributions(["cal:hour"], [1.0, 2.0], bias=0.0)


def test_gru_step_follows_the_names() -> None:
    step = gru_step_features(window(T0), LAYOUT)
    assert len(step) == len(gru_feature_names(LAYOUT)) == 3 + 3 + 1 + 4
    assert step[:7] == [50.0, 51.0, 52.0, 20.0, 21.0, 22.0, 3.0]


def test_gru_calendar_is_on_the_unit_circle() -> None:
    h_sin, h_cos, w_sin, w_cos = gru_calendar(T0)
    assert h_sin**2 + h_cos**2 == pytest.approx(1.0)
    assert w_sin**2 + w_cos**2 == pytest.approx(1.0)
    assert math.atan2(h_sin, h_cos) == pytest.approx(2 * math.pi * hour_of_day(T0) / 24)


def test_portable_booster_json_makes_base_score_scalar() -> None:

    raw = json.dumps({"learner": {"learner_model_param": {"base_score": "[8.249473E-1]"}}})
    out = json.loads(portable_booster_json(raw))
    assert out["learner"]["learner_model_param"]["base_score"] == "8.249473E-1"
    scalar = json.dumps({"learner": {"learner_model_param": {"base_score": "5E-1"}}})
    assert json.loads(portable_booster_json(scalar)) == json.loads(scalar)


def test_portable_booster_json_rejects_multi_target() -> None:

    raw = json.dumps({"learner": {"learner_model_param": {"base_score": "[1E-1,2E-1]"}}})
    with pytest.raises(ValueError):
        portable_booster_json(raw)


def test_canonical_id_strips_column_prefixes() -> None:
    assert canonical_id("Pressure_n1") == "n1"
    assert canonical_id("Flow_PUMP_1") == "PUMP_1"
    assert canonical_id("Level_T1") == "T1"
    assert canonical_id("n1") == "n1"


def test_translator_maps_both_spellings_to_model_ids() -> None:
    ids = IdTranslator(LAYOUT)
    assert ids.to_model("n1") == "Pressure_n1"
    assert ids.to_model("Pressure_n1") == "Pressure_n1"
    assert ids.to_model("PUMP_1") == "Flow_PUMP_1"
    assert ids.to_model("unknown") == "unknown"


def test_translator_window_renames_only_when_needed() -> None:
    ids = IdTranslator(LAYOUT)
    aligned = window(T0)
    assert ids.window(aligned) is aligned
    short = short_ids(request(T0)).current
    renamed = ids.window(short)
    assert renamed.pressures == aligned.pressures
    assert renamed.flows == aligned.flows
    assert renamed.tank_levels == aligned.tank_levels
