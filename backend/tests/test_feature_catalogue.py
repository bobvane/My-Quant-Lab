"""The feature catalogue is the code, so the code must agree with it (ADR-093).

``GET /features`` used to read the ``features`` table. Nothing in the repository ever
wrote a row into that table, so the endpoint answered ``[]`` for the entire life of the
project while the engine happily computed 32 features — a client asking "which features
does this engine produce?" got an empty answer, and the ``definition_versions`` field of
``GET /features/versions`` was empty for the same reason.

The catalogue now lives in ``app/features/catalogue.py`` and is checked *both ways*
against what ``build_features()`` actually returns: a feature that is computed but not
listed is a documentation defect, and a feature that is listed but not computed is a lie
in the API. Either direction fails here.
"""

from __future__ import annotations

import pandas as pd

from app.features.catalogue import FEATURE_CATALOGUE, catalogue_payload
from app.features.engine import FEATURE_VERSION, OHLCV_COLUMNS, build_features


def _computed_columns(bars: pd.DataFrame) -> set[str]:
    features = build_features(bars)
    return set(features.columns) - set(OHLCV_COLUMNS)


def test_every_computed_feature_is_catalogued(sample_bars: pd.DataFrame) -> None:
    computed = _computed_columns(sample_bars)

    assert computed - {spec.name for spec in FEATURE_CATALOGUE} == set()


def test_every_catalogued_feature_is_computed(sample_bars: pd.DataFrame) -> None:
    computed = _computed_columns(sample_bars)

    assert {spec.name for spec in FEATURE_CATALOGUE} - computed == set()


def test_every_catalogue_entry_is_complete() -> None:
    for spec in FEATURE_CATALOGUE:
        assert spec.name, "a feature without a name is not a feature"
        assert spec.family in {"indicator", "price_action"}, spec.name
        assert spec.description, f"{spec.name} must explain what it means"
        assert spec.inputs, f"{spec.name} must name the columns it reads"
        assert set(spec.inputs) <= set(OHLCV_COLUMNS), spec.name
        assert spec.feature_version == FEATURE_VERSION, spec.name


def test_the_names_are_unique() -> None:
    names = [spec.name for spec in FEATURE_CATALOGUE]

    assert len(names) == len(set(names))


def test_the_payload_is_sorted_and_has_no_database_identity() -> None:
    payload = catalogue_payload()

    assert [row["name"] for row in payload] == sorted(row["name"] for row in payload)
    # There is no row and therefore no id: handing one out would invite a client to
    # treat a code-defined catalogue as something it can address by primary key.
    assert all("id" not in row for row in payload)
    assert all(row["feature_version"] == FEATURE_VERSION for row in payload)


def test_the_endpoint_serves_the_catalogue(client) -> None:
    body = client.get("/api/v1/features").json()

    assert {row["name"] for row in body} == {spec.name for spec in FEATURE_CATALOGUE}
