import pytest

from app.digits import said_code, spoken_digits


@pytest.mark.parametrize(
    ("heard", "digits"),
    [
        ("I approve this request, code 4719.", "4719"),
        ("I approve this request, code 4, 7, 1, 9.", "4719"),
        ("I approve this request code four seven one nine", "4719"),
        ("code four seven oh nine", "4709"),
        ("I approve this request", ""),
    ],
)
def test_spoken_digits(heard, digits):
    assert spoken_digits(heard) == digits


def test_the_code_must_appear_in_order():
    assert said_code("I approve this request, code 4719", "4719")
    assert not said_code("I approve this request, code 4791", "4719")
    assert not said_code("I approve this request", "4719")
