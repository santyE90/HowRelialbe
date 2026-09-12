"""Behavioral tests for the canonical vehicle domain model."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from howreliable.domain import Drivetrain, Transmission, Vehicle


def make_vehicle(**overrides: object) -> Vehicle:
    values: dict[str, object] = {
        "make": "Mazda",
        "model": "Mazda3",
        "year": 2017,
        "generation": "BM/BN",
        "trim": "GT",
        "engine": "2.5L I4",
        "transmission": "automatic",
        "drivetrain": "fwd",
    }
    values.update(overrides)
    return Vehicle.model_validate(values)


def test_valid_vehicle_is_normalized_and_immutable() -> None:
    vehicle = make_vehicle()

    assert vehicle.make == "mazda"
    assert vehicle.model == "mazda3"
    assert vehicle.year == 2017
    assert vehicle.transmission is Transmission.AUTOMATIC
    assert vehicle.drivetrain is Drivetrain.FWD
    with pytest.raises(ValidationError):
        vehicle.year = 2018


@pytest.mark.parametrize("field", ["make", "model"])
@pytest.mark.parametrize("value", [None, "", "   \t\n"])
def test_required_text_rejects_missing_or_empty_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_vehicle(**{field: value})


@pytest.mark.parametrize("year", [1885, 2101, True, "2017"])
def test_year_rejects_out_of_policy_or_non_integer_values(year: object) -> None:
    with pytest.raises(ValidationError):
        make_vehicle(year=year)


@pytest.mark.parametrize("year", [1886, 2027, 2100])
def test_year_accepts_documented_static_range(year: int) -> None:
    assert make_vehicle(year=year).year == year


def test_descriptive_text_normalizes_case_unicode_and_whitespace() -> None:
    vehicle = make_vehicle(
        make="  MAZDA  ",
        model=" Mazda3\tSport ",
        generation="  BM / BN ",
        trim=" G\uff34 ",
        engine=" 2.0 L   TURBO ",
    )

    assert vehicle.make == "mazda"
    assert vehicle.model == "mazda3 sport"
    assert vehicle.generation == "bm / bn"
    assert vehicle.trim == "gt"
    assert vehicle.engine == "2.0 l turbo"


@pytest.mark.parametrize("field", ["generation", "trim", "engine"])
def test_optional_fields_accept_none_but_reject_empty_text(field: str) -> None:
    assert getattr(make_vehicle(**{field: None}), field) is None
    with pytest.raises(ValidationError):
        make_vehicle(**{field: "  "})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("automatic", Transmission.AUTOMATIC),
        ("Automatic", Transmission.AUTOMATIC),
        ("AUTO", Transmission.AUTOMATIC),
        ("manual", Transmission.MANUAL),
        ("continuously-variable transmission", Transmission.CVT),
        ("dual clutch transmission", Transmission.DCT),
        ("other", Transmission.OTHER),
        ("unknown", Transmission.UNKNOWN),
    ],
)
def test_transmission_aliases(raw: str, expected: Transmission) -> None:
    assert make_vehicle(transmission=raw).transmission is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FWD", Drivetrain.FWD),
        ("front wheel drive", Drivetrain.FWD),
        ("front-wheel drive", Drivetrain.FWD),
        ("RWD", Drivetrain.RWD),
        ("rear-wheel-drive", Drivetrain.RWD),
        ("AWD", Drivetrain.AWD),
        ("all wheel drive", Drivetrain.AWD),
        ("4WD", Drivetrain.FOUR_WD),
        ("four-wheel drive", Drivetrain.FOUR_WD),
        ("other", Drivetrain.OTHER),
        ("unknown", Drivetrain.UNKNOWN),
    ],
)
def test_drivetrain_aliases(raw: str, expected: Drivetrain) -> None:
    assert make_vehicle(drivetrain=raw).drivetrain is expected


@pytest.mark.parametrize(
    ("field", "value"),
    [("transmission", "tiptronic"), ("drivetrain", "part-time sometimes")],
)
def test_unsupported_categories_are_not_guessed(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match=f"unsupported {field} value"):
        make_vehicle(**{field: value})


def test_serialization_is_predictable_and_uses_canonical_values() -> None:
    vehicle = make_vehicle(generation=None, engine=" Electric ", transmission="AUTO")

    assert vehicle.model_dump(mode="json") == {
        "make": "mazda",
        "model": "mazda3",
        "year": 2017,
        "generation": None,
        "trim": "gt",
        "engine": "electric",
        "transmission": "automatic",
        "drivetrain": "fwd",
    }
    assert json.loads(vehicle.configuration_key) == vehicle.model_dump(mode="json")


def test_equivalent_raw_values_have_equal_configuration_identity() -> None:
    first = make_vehicle(
        make="mazda",
        model="Mazda3",
        generation=None,
        trim="GT",
        engine="2.5L",
        transmission="automatic",
        drivetrain="fwd",
    )
    second = make_vehicle(
        make=" MAZDA ",
        model="Mazda3",
        generation=None,
        trim=" gt ",
        engine=" 2.5l ",
        transmission="AUTO",
        drivetrain="front-wheel drive",
    )

    assert first == second
    assert first.configuration_key == second.configuration_key
    assert first.configuration_id == second.configuration_id


def test_material_configuration_difference_changes_only_configuration_identity() -> None:
    automatic = make_vehicle(transmission="automatic")
    manual = make_vehicle(transmission="manual")

    assert automatic.broad_identity_id == manual.broad_identity_id
    assert automatic.configuration_id != manual.configuration_id


def test_generation_changes_broad_and_configuration_identity() -> None:
    first = make_vehicle(generation="first")
    second = make_vehicle(generation="second")

    assert first.broad_identity_id != second.broad_identity_id
    assert first.configuration_id != second.configuration_id


def test_configuration_id_is_independent_of_python_hash_randomization() -> None:
    script = """
from howreliable.domain import Vehicle
vehicle = Vehicle(
    make=' MAZDA ', model='Mazda3', year=2017, generation=None, trim='GT',
    engine='2.5L', transmission='AUTO', drivetrain='front-wheel drive'
)
print(vehicle.configuration_id)
"""

    ids = []
    for seed in ("1", "987654"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        ids.append(result.stdout.strip())

    assert ids[0] == ids[1] == make_vehicle(generation=None, engine="2.5L").configuration_id
