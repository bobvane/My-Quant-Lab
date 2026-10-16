"""Extract the real Lab functions from LabView.vue into a runnable ES module.

The bodies are copied verbatim. Three mechanical rewrites make them runnable
under plain Node outside a Vue component:

1. ``api.aiResearchRun`` / ``api.experiment`` / ``ApiError`` are routed through
   ``__tickApi`` so the transport can have its response order controlled;
2. TypeScript-only syntax (parameter annotations, ``as`` casts, ``const x: T``)
   is removed, because Node executes this as ESM rather than compiling it;
3. the reactive state the bodies close over is imported from
   ``frontend/lab_race_env.mjs`` -- imported, not inlined, so the bodies and
   the scenario harness share one instance.

Anything missing from the source is a hard error: a silent empty extraction
would let the race test pass without ever running the shipped code.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LAB = REPO_ROOT / "frontend" / "src" / "views" / "LabView.vue"
ENV = REPO_ROOT / "frontend" / "lab_race_env.mjs"

FUNCTIONS = [
    "tick",
    "openRun",
    "refreshRun",
    "openExperimentById",
    "closeExperiment",
]

#: Async entry points that write the shown research. They are extracted and run
#: too: they used to be the unguarded half of the same defect, and the harness
#: needs their real bodies to prove they now share runRequestSeq.
ACTION_FUNCTIONS = [
    "formalize",
    "confirmDraft",
    "handleCompileFailure",
]

#: The same defect on the experiment side: createExperiment() re-reads the
#: record it just made, which yanks the user off whatever they opened while
#: the engine was still running.
EXPERIMENT_FUNCTIONS = ["createExperiment"]

QUOTES = (chr(39), chr(34), chr(96))


def _match_brace(text: str, open_at: int) -> int:
    """Return the index of the brace closing the one at open_at."""
    depth = 0
    i = open_at
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        elif ch in QUOTES:
            quote = ch
            i += 1
            while i < len(text) and text[i] != quote:
                if text[i] == chr(92):
                    i += 1
                i += 1
        i += 1
    raise AssertionError("unbalanced braces")


def _extract_function(text: str, name: str) -> str:
    pattern = re.compile(r"^(async )?function " + re.escape(name) + r"\(", re.MULTILINE)
    match = pattern.search(text)
    if match is None:
        raise AssertionError(f"{name}() not found in LabView.vue")
    brace = text.index("{", match.end() - 1)
    end = _match_brace(text, brace)
    return text[match.start() : end + 1]


def _extract_counter(text: str, name: str) -> str:
    pattern = re.compile(r"^let " + re.escape(name) + r" = 0$", re.MULTILINE)
    match = pattern.search(text)
    if match is None:
        raise AssertionError(f"{name} counter not found in LabView.vue")
    return match.group(0)


def strip_types(body: str) -> str:
    """Remove the TypeScript-only syntax Node cannot parse.

    Three shapes appear in the extracted functions, and each becomes something
    that means the same thing at runtime: a parameter annotation, an ``as`` cast
    and a ``const x: T =`` declaration. Control flow is left byte-for-byte
    alone, so what runs here is what ships.

    The casts are matched narrowly on purpose. A greedy rule would also happily
    eat ``foo(bar as Baz)`` into ``foobar`` -- which silently changes what the
    tested code *does* rather than only what it looks like.
    """
    body = re.sub(r"\((\w+): [A-Za-z][\w<>\[\]| ]*\)", r"(\1)", body)
    # A parameter list may carry more than one annotation (handleCompileFailure).
    body = re.sub(
        r"\((\w+): [A-Za-z][\w<>\[\]| ]*, (\w+): [A-Za-z][\w<>\[\]| ]*\)",
        r"(\1, \2)",
        body,
    )
    # `x as T`: an identifier operand, optionally parenthesised, never a call.
    body = re.sub(r"\((\w+)\) as [A-Za-z_][\w.\[\]| <>]*", r"(\1)", body)
    body = re.sub(r"(?<![\w.])(\w+) as [A-Za-z_][\w.\[\]| <>]*", r"\1", body)
    # `(a ?? b) as Record<string, any> | null`: a parenthesised expression whose
    # type may itself contain angle brackets and a union. The angle-bracket
    # part is matched separately so `Record<string, any>` cannot end the class.
    body = re.sub(
        r"(\((?:[^()]|\([^()]*\))*\)) as [A-Za-z_][\w.\[\]| ]*<[^<>]*>([\w.\[\]| ]*)",
        r"\1",
        body,
    )
    body = re.sub(r"(\((?:[^()]|\([^()]*\))*\)) as [A-Za-z_][\w.\[\]| ]*", r"\1", body)
    body = re.sub(r"\b(const|let) (\w+): [^=\n]+=", r"\1 \2 =", body)
    return body


def environment_import() -> str:
    """Return the import of the shared reactive environment.

    The names come from ENV_NAMES in the environment module itself, so this
    import cannot drift from what that module actually exports.
    """
    text = ENV.read_text(encoding="utf-8")
    block = re.search(r"export const ENV_NAMES = \[(.*?)\]", text, re.S)
    if block is None:
        raise AssertionError("ENV_NAMES missing from lab_race_env.mjs")
    names = re.findall(chr(39) + r"(\w+)" + chr(39), block.group(1))
    lines = ["import {"]
    for name in names:
        lines.append("  " + name + ",")
    lines.append(chr(125) + " from " + chr(39) + "./lab_race_env.mjs" + chr(39))
    return chr(10).join(lines)


#: Calls the action functions make that the scenarios do not need to observe.
#: They are replaced with an inert `await null` so the extracted body keeps its
#: control flow without dragging in a modelled transport for every endpoint.
UNMODELLED_API = re.compile(r"\bapi\.(?!aiResearchRun|experiment\b|createExperiment\b)\w+\s*\(")


def _stub_unmodelled_api(body: str) -> str:
    return UNMODELLED_API.sub("__tickApi.unmodelled(", body)


def build_module() -> str:
    """Return an ES module holding the shipped function bodies."""
    text = LAB.read_text(encoding="utf-8")

    parts = [
        "// GENERATED at test time from frontend/src/views/LabView.vue.",
        "// Function bodies below are the shipped ones; only TS syntax is stripped.",
        "",
        environment_import(),
        "",
    ]
    for counter in ("runRequestSeq", "experimentRequestSeq"):
        parts.append(_extract_counter(text, counter))
    parts.append("")

    for name in FUNCTIONS + ACTION_FUNCTIONS + EXPERIMENT_FUNCTIONS:
        body = strip_types(_extract_function(text, name))
        body = body.replace("api.aiResearchRun", "__tickApi.aiResearchRun")
        body = body.replace("api.experiment(", "__tickApi.experiment(")
        body = body.replace("api.createExperiment(", "__tickApi.createExperiment(")
        body = re.sub(r"\bApiError\b", "__tickApi.ApiError", body)
        body = _stub_unmodelled_api(body)
        parts.append(body)
        parts.append("")

    parts.append("// Lets a scenario take a ticket exactly the way compile() would.")
    parts.append("export function takeRunTicket() { return ++runRequestSeq }")

    parts.append("export {")
    for name in FUNCTIONS + ACTION_FUNCTIONS + EXPERIMENT_FUNCTIONS:
        parts.append("  " + name + ",")
    parts.append("}")
    parts.append("")
    return chr(10).join(parts)
