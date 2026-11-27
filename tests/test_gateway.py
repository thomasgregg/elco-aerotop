"""Verify that gateway input cannot change the shape of a Remocon request."""

import pytest

from custom_components.elco_aerotop.gateway import normalize_gateway_id, plant_gateway_ids


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("  demo-123_4  ", "DEMO-123_4"),
        ("demo-123?navMenuItem=1&breadcrumbPath=0#view", "DEMO-123"),
        ("/R2/Plant/Index/demo-123?navMenuItem=1", "DEMO-123"),
        ("r2/plant/index/demo-123/", "DEMO-123"),
        ("https://example.invalid/R2/Plant/Index/demo-123#view", "DEMO-123"),
        ("https://example.invalid/R2/BsbPlantDashboard/Index/%64emo-123/", "DEMO-123"),
        ("DEMO~123.4", "DEMO~123.4"),
    ],
)
def test_gateway_id_is_normalized_and_idempotent(supplied, expected):
    assert normalize_gateway_id(supplied) == expected
    assert normalize_gateway_id(expected) == expected


@pytest.mark.parametrize(
    "supplied",
    [
        "",
        "  ",
        "#missing",
        "?gatewayId=demo",
        ".",
        "%2e%2e",
        "demo/id",
        "demo%2fid",
        "demo%3Fid",
        "demo%252fid",
        "demo%00id",
        "demo%23id",
        "demo&id",
        "demo\\id",
        "demo\nid",
        "demo id",
        "démó",
        "//example.invalid/R2/Plant/Index/demo",
        "https:/R2/Plant/Index/demo",
        "https://example.invalid",
        "https://example.invalid/R2/Plant/Index/",
        "https://example.invalid/R2/Plant/Index/demo/more",
        "https://example.invalid/R2/Account/Login/demo",
        "https://someone@example.invalid/R2/Plant/Index/demo",
        "https://:password@example.invalid/R2/Plant/Index/demo",
        "https://[invalid/R2/Plant/Index/demo",
        "ftp://example.invalid/R2/Plant/Index/demo",
    ],
)
def test_invalid_gateway_ids_are_rejected(supplied):
    with pytest.raises(ValueError):
        normalize_gateway_id(supplied)


def test_plant_gateway_ids_handles_aliases_and_invalid_fields():
    assert plant_gateway_ids({"gwId": " demo ", "gwSerial": "alias"}) == ("DEMO", "ALIAS")
    assert plant_gateway_ids({"gwId": "demo", "gwSerial": "DEMO"}) == ("DEMO",)
    assert plant_gateway_ids({"gwId": "invalid/value", "gwSerial": 1234}) == ("1234",)
    assert plant_gateway_ids({"gwId": True, "gwSerial": {"id": "demo"}}) == ()
