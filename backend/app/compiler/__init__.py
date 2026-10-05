"""The deterministic Strategy Compiler (docs/29, ADR-167, ADR-168).

Public surface:

* :func:`compile_strategy_draft` - the single entry point, a pure function.
* :class:`CompilerInput`, :class:`CompileResult`, :class:`DraftView` - its types.
* :data:`COMPILER_VERSION`, :data:`REJECTION_CODES`, :data:`DECIDED_BY`,
  :data:`RESULTS`, :data:`UNREACHABLE_IN_STEP_2B` - the frozen contract values.

Nothing here reads or writes a database, calls a model, or reaches the network;
persisting a result is the caller's job (docs/29 section 19).
"""

from app.compiler.compiler import compile_strategy_draft
from app.compiler.errors import (
    COMPILER_VERSION,
    DECIDED_BY,
    DECISIONS,
    REJECTION_CODES,
    RESULTS,
    STRUCTURAL_CODES,
    UNREACHABLE_IN_STEP_2B,
    USER_DECIDABLE_CODES,
    Rejection,
    RejectionLog,
)
from app.compiler.hashing import compile_hash, draft_hash, project
from app.compiler.models import CompileResult, CompilerInput, DraftView

__all__ = [
    "COMPILER_VERSION",
    "DECIDED_BY",
    "DECISIONS",
    "REJECTION_CODES",
    "RESULTS",
    "STRUCTURAL_CODES",
    "UNREACHABLE_IN_STEP_2B",
    "USER_DECIDABLE_CODES",
    "CompileResult",
    "CompilerInput",
    "DraftView",
    "Rejection",
    "RejectionLog",
    "compile_hash",
    "compile_strategy_draft",
    "draft_hash",
    "project",
]
