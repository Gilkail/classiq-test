"""Pure Qiskit execution service.

Phase 3 fills this in with ``run_circuit(qasm, shots) -> dict[str, int]``,
deserializing via ``qiskit.qasm3.loads`` and running on ``AerSimulator``,
raising typed errors (``InvalidQasmError`` / ``ExecutionError``) per REFERENCE §8.
"""

# TODO(Phase 3): implement run_circuit() + typed error classes.
