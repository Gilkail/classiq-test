"""Pure Qiskit execution service (REFERENCE §4.2, §8).

Deserializes an OpenQASM 3 string and runs it on ``AerSimulator``, returning a
counts dict. Failures are surfaced as two typed errors so the caller can map
them to the right terminal state:

* ``InvalidQasmError``  -> ``error_kind=invalid_qasm3`` (F2)
* ``ExecutionError``    -> ``error_kind=execution_error`` (F3)

No Redis or RQ coupling here; this module is import-safe and unit-testable.
"""

from __future__ import annotations

import qiskit.qasm3
from qiskit import transpile
from qiskit.qasm3 import QASM3ImporterError
from qiskit_aer import AerSimulator
from openqasm3.parser import QASM3ParsingError


class InvalidQasmError(Exception):
    """Raised when the QASM3 payload fails to parse (malformed syntax)."""


class ExecutionError(Exception):
    """Raised when transpilation or simulation fails for any other reason."""


def run_circuit(qasm: str, shots: int) -> dict[str, int]:
    """Parse, transpile, and simulate ``qasm``; return measurement counts."""
    # Parse first so syntax errors are distinguished from execution faults (F2).
    try:
        circuit = qiskit.qasm3.loads(qasm)
    except (QASM3ImporterError, QASM3ParsingError) as exc:
        # QASM3ImporterError: valid syntax, un-convertible to a circuit.
        # QASM3ParsingError: malformed syntax (raised by the ANTLR parser and
        # NOT wrapped by loads()). Both are bad input -> F2.
        raise InvalidQasmError("Invalid QASM3 syntax") from exc

    # Transpile + run; any fault here is an execution error (F3).
    try:
        simulator = AerSimulator()
        transpiled = transpile(circuit, simulator)
        result = simulator.run(transpiled, shots=shots).result()
        counts = result.get_counts()
    except Exception as exc:  # broad by design: simulator faults are opaque
        raise ExecutionError(str(exc)) from exc

    # Normalize to plain str->int (Qiskit may return a Counts subclass).
    return {str(key): int(value) for key, value in counts.items()}
