import pytest

from wda.engine.formula import QueryError, compile_formula


@pytest.mark.parametrize(("text", "values", "expected"), [
    ("a + b", {"a": 2, "b": 3}, 5),
    ("gesamt - mit_lizenz", {"gesamt": 100, "mit_lizenz": 70}, 30),
    ("a + b * 2", {"a": 1, "b": 3}, 7),
    ("(a + b) * 2", {"a": 1, "b": 3}, 8),
    ("-a + 10", {"a": 4}, 6),
    ("a / b", {"a": 1, "b": 4}, 0.25),
    ("a / b * 100", {"a": 1, "b": 4}, 25),
    ("a / b", {"a": 1, "b": 0}, None),
    ("a + b", {"a": 1, "b": None}, None),
    ("a + fehlt", {"a": 1}, None),
    ("round(a, -2)", {"a": 1349}, 1300),
    ("round(a, -2)", {"a": 1350}, 1400),
    ("round(a, -3)", {"a": 74500}, 75000),
    ("round(a / b * 100, 1)", {"a": 1, "b": 3}, 33.3),
    ("round(a)", {"a": 2.5}, 3),
    ("round(a)", {"a": -2.5}, -3),
    ("max(a, b, 0)", {"a": -4, "b": -2}, 0),
    ("min(a, b)", {"a": 4, "b": 2}, 2),
    ("1e308 * 10 * a", {"a": 10}, None),
    ("round(a, b)", {"a": 1.5, "b": 0.5}, None),
])
def test_evaluate(text, values, expected):
    assert compile_formula(text).evaluate(values) == expected


def test_names():
    assert compile_formula("round(a / (b + c), 2) - a").names == frozenset({"a", "b", "c"})


@pytest.mark.parametrize("text", [
    "", "   ", "__import__('os').system('id')", "a.b", "a ** 9 ** 9", "[1]", "'x'", "a if b else c",
    "a > b", "lambda: 1", "round", "round(a, digits=1)", "round()", "abs(a)", "a(b)", "a +",
    "True + 1", "a; b", "x" * 501, "(" * 300 + "a" + ")" * 300, "round(*a)", "a @ b", "a // b",
    "a % b", "f'{a}'", "max(1,2,3,4,5,6,7,8,9)", "1j", "a := 1",
])
def test_rejected(text):
    with pytest.raises(QueryError):
        compile_formula(text)
