from __future__ import annotations

import pytest

from lattice.tools.calculator import CalculatorError, calculate


def test_calculator_basic_arithmetic() -> None:
    assert calculate("(2 + 3) * 4") == "20"
    assert calculate("7 / 2") == "3.5"
    assert calculate("2 ** 10") == "1024"
    assert calculate("-5 + 2") == "-3"
    assert calculate("2 + 3 * 4") == "14"


def test_calculator_functions_and_constants() -> None:
    assert calculate("sqrt(16)") == "4"
    assert calculate("round(pi, 2)") == "3.14"
    assert calculate("max(3, 7) - min(1, 5)") == "6"


def test_calculator_rejects_code_and_bad_math() -> None:
    with pytest.raises(CalculatorError):
        calculate("__import__('os').system('echo hi')")
    with pytest.raises(CalculatorError):
        calculate("open('/etc/passwd')")
    with pytest.raises(CalculatorError):
        calculate("1 / 0")
    with pytest.raises(CalculatorError):
        calculate("(1).__class__")
    with pytest.raises(CalculatorError):
        calculate("")
    with pytest.raises(CalculatorError):
        calculate("2 ** 5000")


def test_calculator_is_registered() -> None:
    from lattice.deps import CORE_TOOL_NAMES
    from lattice.tools.agent import tool_functions

    assert "calculator" in CORE_TOOL_NAMES
    assert "calculator" in tool_functions()
