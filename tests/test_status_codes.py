import pytest

from responder import status_codes


@pytest.mark.parametrize(
    "status_code, expected",
    [
        pytest.param(101, True, id="Normal 101"),
        pytest.param(199, True, id="Not actual status code but within 100"),
        pytest.param(0, False, id="Zero case (below 100)"),
        pytest.param(200, False, id="Above 100"),
    ],
)
def test_is_100(status_code, expected):
    assert status_codes.is_100(status_code) is expected


@pytest.mark.parametrize(
    "status_code, expected",
    [
        pytest.param(201, True, id="Normal 201"),
        pytest.param(299, True, id="Not actual status code but within 200"),
        pytest.param(0, False, id="Zero case (below 200)"),
        pytest.param(300, False, id="Above 200"),
    ],
)
def test_is_200(status_code, expected):
    assert status_codes.is_200(status_code) is expected


@pytest.mark.parametrize(
    "status_code, expected",
    [
        pytest.param(301, True, id="Normal 301"),
        pytest.param(399, True, id="Not actual status code but within 300"),
        pytest.param(0, False, id="Zero case (below 300)"),
        pytest.param(400, False, id="Above 300"),
    ],
)
def test_is_300(status_code, expected):
    assert status_codes.is_300(status_code) is expected


@pytest.mark.parametrize(
    "status_code, expected",
    [
        pytest.param(401, True, id="Normal 401"),
        pytest.param(499, True, id="Not actual status code but within 400"),
        pytest.param(0, False, id="Zero case (below 400)"),
        pytest.param(500, False, id="Above 400"),
    ],
)
def test_is_400(status_code, expected):
    assert status_codes.is_400(status_code) is expected


@pytest.mark.parametrize(
    "status_code, expected",
    [
        pytest.param(501, True, id="Normal 501"),
        pytest.param(599, True, id="Not actual status code but within 500"),
        pytest.param(0, False, id="Zero case (below 500)"),
        pytest.param(600, False, id="Above 500"),
    ],
)
def test_is_500(status_code, expected):
    assert status_codes.is_500(status_code) is expected
