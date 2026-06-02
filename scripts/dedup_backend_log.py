#!/usr/bin/env python3
"""Deduplicate the backend.log slice of a mixed evidence file into Markdown.

The output is designed for humans and LLMs first, but it is also self-contained:
each family stores a normalized template plus per-occurrence values, which allows
the backend slice to be reconstructed exactly from the Markdown file alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field


DEFAULT_ANCHOR = "backend.log"

HEADER_RE = re.compile(
    r"^\[(?P<timestamp>[^\]]+)\] \[(?P<thread>[^\]]+)\] "
    r"\[(?P<level>[^\]]+)\] \[(?P<logger>[^\]]+)\]  - (?P<message>.*)$"
)
BACKEND_HEADER_RE = re.compile(r"^\[[0-9]{4}/[0-9]{2}/[0-9]{2}-[0-9:.]+\] ")
PROCESS_STATS_RE = re.compile(r"^(Process stats for pid )(\d+)(: )(\{.*\})$")
CRU_JSON_RE = re.compile(r"^(Reporting completion of CRU:)(\{.*\})$")
PLACEHOLDER_RE = re.compile(r"<([A-Z0-9_]+)>")


@dataclass(frozen=True)
class PatternSpec:
    name: str
    regex: re.Pattern[str]
    replacement_template: str = "{token}"
    value_group: int = 1


@dataclass
class EventBlock:
    seq: int
    start_line: int
    raw_lines: list[str]
    timestamp: str
    thread: str
    level: str
    logger: str

    @property
    def raw_text(self) -> str:
        return "\n".join(self.raw_lines)


@dataclass
class Occurrence:
    seq: int
    start_line: int
    values: dict[str, str]

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "seq": self.seq,
            "start_line": self.start_line,
        }
        data.update(self.values)
        return data


@dataclass
class Family:
    template: str
    level: str
    logger: str
    occurrences: list[Occurrence] = field(default_factory=list)
    family_id: str = ""

    @property
    def count(self) -> int:
        return len(self.occurrences)

    @property
    def first_seq(self) -> int:
        return self.occurrences[0].seq

    @property
    def last_seq(self) -> int:
        return self.occurrences[-1].seq

    @property
    def first_line(self) -> int:
        return self.occurrences[0].start_line

    @property
    def last_line(self) -> int:
        return self.occurrences[-1].start_line

    @property
    def first_timestamp(self) -> str:
        return str(self.occurrences[0].values.get("TIMESTAMP_1", ""))

    @property
    def last_timestamp(self) -> str:
        return str(self.occurrences[-1].values.get("TIMESTAMP_1", ""))

    @property
    def placeholder_names(self) -> list[str]:
        if not self.occurrences:
            return []
        return [
            key
            for key in self.occurrences[0].as_dict().keys()
            if key not in {"seq", "start_line"}
        ]

    @property
    def preview(self) -> str:
        first_line = self.template.splitlines()[0]
        return first_line if len(first_line) <= 120 else first_line[:117] + "..."


@dataclass
class Bundle:
    source_path: str
    anchor_text: str
    anchor_line: int
    backend_lines: list[str]
    blocks: list[EventBlock]
    families: list[Family]
    backend_sha256: str

    @property
    def total_backend_lines(self) -> int:
        return len(self.backend_lines)

    @property
    def total_blocks(self) -> int:
        return len(self.blocks)


PATTERNS: list[PatternSpec] = [
    PatternSpec("CT", re.compile(r"\[ct: (\d+)\]"), "[ct: {token}]"),
    PatternSpec("TIME_MS", re.compile(r"time=(\d+)ms"), "time={token}ms"),
    PatternSpec("SECONDS", re.compile(r"\bin (\d+) seconds\b"), "in {token} seconds"),
    PatternSpec("JOB_ID", re.compile(r"jobId=([A-Za-z0-9]+)"), "jobId={token}"),
    PatternSpec("PROJECT_KEY", re.compile(r"projectKey=([A-Z0-9_]+)"), "projectKey={token}"),
    PatternSpec("ENV_LANG", re.compile(r"envLang=([A-Z]+)"), "envLang={token}"),
    PatternSpec("LANG", re.compile(r"\blang=([A-Z]+)\b"), "lang={token}"),
    PatternSpec(
        "ENV_NAME",
        re.compile(r"envName=([^\] ]+)"),
        "envName={token}",
    ),
    PatternSpec(
        "INTERPRETER",
        re.compile(r"interpreter=([A-Z0-9]+)"),
        "interpreter={token}",
    ),
    PatternSpec(
        "CORE_PACKAGE_SET",
        re.compile(r"\b(PANDAS\d+)\b"),
        "{token}",
    ),
    PatternSpec("PID", re.compile(r"\bpid (\d+)\b"), "pid {token}"),
    PatternSpec(
        "GENERATED_ACCESSOR",
        re.compile(r"(GeneratedMethodAccessor\d+)"),
        "{token}",
    ),
    PatternSpec(
        "SAVED_MODEL_PATH",
        re.compile(
            r"(/data/dataiku/dss_data/saved_models/[^/\s]+/[^/\s]+/versions/v1/core_params\.json)"
        ),
        "{token}",
    ),
    PatternSpec(
        "WEBAPP_FILE_PATH",
        re.compile(r"(/projects/[^/\s]+/web_apps/[^/\s]+\.json)"),
        "{token}",
    ),
    PatternSpec(
        "RECIPE_FILE_PATH",
        re.compile(r"(/projects/[^/\s]+/recipes/[^/\s]+\.json)"),
        "{token}",
    ),
]


def make_token(name: str, value: str, counters: dict[str, int], values: dict[str, str]) -> str:
    counters[name] = counters.get(name, 0) + 1
    key = f"{name}_{counters[name]}"
    values[key] = value
    return f"<{key}>"


def apply_patterns(
    text: str,
    counters: dict[str, int],
    values: dict[str, str],
    patterns: list[PatternSpec] | None = None,
) -> str:
    current = text
    for spec in (patterns or PATTERNS):
        def _replace(match: re.Match[str]) -> str:
            value = match.group(spec.value_group)
            token = make_token(spec.name, value, counters, values)
            return spec.replacement_template.format(token=token)

        current = spec.regex.sub(_replace, current)
    return current


def normalize_content_line(
    line: str,
    counters: dict[str, int],
    values: dict[str, str],
) -> str:
    process_match = PROCESS_STATS_RE.match(line)
    if process_match:
        prefix, pid_value, middle, json_payload = process_match.groups()
        pid_token = make_token("PID", pid_value, counters, values)
        json_token = make_token("PROCESS_STATS_JSON", json_payload, counters, values)
        return f"{prefix}{pid_token}{middle}{json_token}"

    cru_match = CRU_JSON_RE.match(line)
    if cru_match:
        prefix, json_payload = cru_match.groups()
        json_token = make_token("CRU_JSON", json_payload, counters, values)
        return f"{prefix}{json_token}"

    return apply_patterns(line, counters, values)


def normalize_block(block: EventBlock) -> tuple[str, dict[str, str]]:
    header_match = HEADER_RE.match(block.raw_lines[0])
    if not header_match:
        raise ValueError(f"Malformed backend header at line {block.start_line}")

    counters: dict[str, int] = {}
    values: dict[str, str] = {}

    timestamp_token = make_token("TIMESTAMP", header_match.group("timestamp"), counters, values)
    thread_token = make_token("THREAD", header_match.group("thread"), counters, values)
    normalized_message = normalize_content_line(
        header_match.group("message"),
        counters,
        values,
    )

    template_lines = [
        (
            f"[{timestamp_token}] [{thread_token}] "
            f"[{header_match.group('level')}] [{header_match.group('logger')}]  - "
            f"{normalized_message}"
        )
    ]

    for line in block.raw_lines[1:]:
        template_lines.append(normalize_content_line(line, counters, values))

    return "\n".join(template_lines), values


def find_anchor(lines: list[str], anchor_text: str) -> int:
    for index, line in enumerate(lines):
        if line.strip() == anchor_text:
            return index
    raise ValueError(f"Anchor line {anchor_text!r} not found")


def load_backend_slice(
    path: str,
    anchor_text: str = DEFAULT_ANCHOR,
) -> tuple[list[str], int, int]:
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    anchor_index = find_anchor(lines, anchor_text)
    backend_start = anchor_index + 1
    while backend_start < len(lines) and not lines[backend_start].strip():
        backend_start += 1

    backend_lines = lines[backend_start:]
    if not backend_lines:
        raise ValueError(f"No backend lines found after anchor {anchor_text!r}")
    if not BACKEND_HEADER_RE.match(backend_lines[0]):
        raise ValueError(
            f"First line after anchor {anchor_text!r} is not a backend log header: "
            f"{backend_lines[0]!r}"
        )

    return backend_lines, anchor_index + 1, backend_start + 1


def parse_blocks(backend_lines: list[str], first_line_number: int) -> list[EventBlock]:
    blocks: list[EventBlock] = []
    current_lines: list[str] = []
    current_start_line = first_line_number

    for offset, line in enumerate(backend_lines):
        absolute_line = first_line_number + offset
        if BACKEND_HEADER_RE.match(line):
            if current_lines:
                blocks.append(make_block(len(blocks) + 1, current_start_line, current_lines))
            current_lines = [line]
            current_start_line = absolute_line
        else:
            if not current_lines:
                raise ValueError(
                    f"Found non-header backend content before first block at line {absolute_line}: "
                    f"{line!r}"
                )
            current_lines.append(line)

    if current_lines:
        blocks.append(make_block(len(blocks) + 1, current_start_line, current_lines))

    if not blocks:
        raise ValueError("No backend blocks were parsed")

    return blocks


def make_block(seq: int, start_line: int, raw_lines: list[str]) -> EventBlock:
    header_match = HEADER_RE.match(raw_lines[0])
    if not header_match:
        raise ValueError(f"Malformed backend header at line {start_line}: {raw_lines[0]!r}")
    return EventBlock(
        seq=seq,
        start_line=start_line,
        raw_lines=list(raw_lines),
        timestamp=header_match.group("timestamp"),
        thread=header_match.group("thread"),
        level=header_match.group("level"),
        logger=header_match.group("logger"),
    )


def build_families(blocks: list[EventBlock]) -> list[Family]:
    families_by_template: dict[str, Family] = {}

    for block in blocks:
        template, values = normalize_block(block)
        family = families_by_template.get(template)
        if family is None:
            family = Family(
                template=template,
                level=block.level,
                logger=block.logger,
            )
            families_by_template[template] = family
        family.occurrences.append(
            Occurrence(
                seq=block.seq,
                start_line=block.start_line,
                values=values,
            )
        )

    families = sorted(families_by_template.values(), key=lambda family: family.first_seq)
    for index, family in enumerate(families, start=1):
        family.family_id = f"F{index:04d}"
    return families


def build_bundle(path: str, anchor_text: str = DEFAULT_ANCHOR) -> Bundle:
    backend_lines, anchor_line, first_backend_line = load_backend_slice(path, anchor_text)
    blocks = parse_blocks(backend_lines, first_backend_line)
    families = build_families(blocks)
    sha256 = hashlib.sha256("\n".join(backend_lines).encode("utf-8")).hexdigest()
    return Bundle(
        source_path=os.path.abspath(path),
        anchor_text=anchor_text,
        anchor_line=anchor_line,
        backend_lines=backend_lines,
        blocks=blocks,
        families=families,
        backend_sha256=sha256,
    )


def render_markdown(bundle: Bundle) -> str:
    lines: list[str] = []
    lines.append("# Backend Log LLM Zip")
    lines.append("")
    lines.append("## Overview")
    lines.append(f"- Source: `{bundle.source_path}`")
    lines.append(f"- Anchor: `{bundle.anchor_text}` at line {bundle.anchor_line}")
    lines.append(f"- Backend lines: {bundle.total_backend_lines}")
    lines.append(f"- Event blocks: {bundle.total_blocks}")
    lines.append(f"- Families: {len(bundle.families)}")
    lines.append(f"- Backend SHA-256: `{bundle.backend_sha256}`")
    lines.append(
        "- Reconstruction note: the template and occurrence rows below are sufficient to rebuild the backend slice exactly."
    )
    lines.append("")
    lines.append("## Top Families")
    lines.append("")
    lines.append("| Family | Count | Level | Logger | Preview |")
    lines.append("| --- | ---: | --- | --- | --- |")
    for family in sorted(bundle.families, key=lambda item: (-item.count, item.first_seq))[:20]:
        preview = family.preview.replace("|", "\\|")
        lines.append(
            f"| {family.family_id} | {family.count} | {family.level} | {family.logger} | {preview} |"
        )

    lines.append("")
    lines.append("## Families")
    lines.append("")

    for family in bundle.families:
        lines.append(f"### {family.family_id}")
        lines.append(f"- Count: {family.count}")
        lines.append(f"- First seq: {family.first_seq}")
        lines.append(f"- Last seq: {family.last_seq}")
        lines.append(f"- First line: {family.first_line}")
        lines.append(f"- Last line: {family.last_line}")
        lines.append(f"- First timestamp: `{family.first_timestamp}`")
        lines.append(f"- Last timestamp: `{family.last_timestamp}`")
        lines.append(f"- Logger: `{family.logger}`")
        lines.append(f"- Level: `{family.level}`")
        lines.append("")
        lines.append("#### Template")
        lines.append("```text")
        lines.extend(family.template.splitlines())
        lines.append("```")
        lines.append("")
        lines.append("#### Fields")
        if family.placeholder_names:
            lines.append(", ".join(f"`{name}`" for name in family.placeholder_names))
        else:
            lines.append("_No placeholders_")
        lines.append("")
        lines.append("#### Occurrences")
        lines.append("```jsonl")
        for occurrence in family.occurrences:
            lines.append(json.dumps(occurrence.as_dict(), separators=(",", ":")))
        lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_block(template: str, values: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"Missing placeholder value for {key!r}")
        return values[key]

    return PLACEHOLDER_RE.sub(_replace, template)


def parse_markdown_families(markdown_text: str) -> list[tuple[str, list[dict[str, object]]]]:
    families: list[tuple[str, list[dict[str, object]]]] = []
    family_re = re.compile(
        r"^### (F\d{4})\n"
        r".*?"
        r"#### Template\n```text\n(?P<template>.*?)\n```\n\n"
        r"#### Fields\n.*?\n\n"
        r"#### Occurrences\n```jsonl\n(?P<occurrences>.*?)\n```",
        re.MULTILINE | re.DOTALL,
    )

    for match in family_re.finditer(markdown_text):
        template = match.group("template")
        raw_occurrences = match.group("occurrences").strip()
        occurrences: list[dict[str, object]] = []
        if raw_occurrences:
            for line in raw_occurrences.splitlines():
                occurrences.append(json.loads(line))
        families.append((template, occurrences))
    return families


def reconstruct_backend_from_markdown(markdown_text: str) -> list[str]:
    rendered: list[tuple[int, str]] = []
    for template, occurrences in parse_markdown_families(markdown_text):
        for occurrence in occurrences:
            values = {
                key: value
                for key, value in occurrence.items()
                if key not in {"seq", "start_line"}
            }
            rendered.append((int(occurrence["seq"]), render_block(template, values)))
    rendered.sort(key=lambda item: item[0])
    return [block_text for _, block_text in rendered]


def default_output_path(input_path: str) -> str:
    root, _ = os.path.splitext(os.path.abspath(input_path))
    return f"{root}.backend.dedup.md"


def write_markdown(bundle: Bundle, output_path: str) -> None:
    markdown = render_markdown(bundle)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(markdown)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to the mixed evidence log")
    parser.add_argument(
        "--anchor",
        default=DEFAULT_ANCHOR,
        help=f"Exact line that marks the start of the backend slice (default: {DEFAULT_ANCHOR})",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output Markdown path (default: <input>.backend.dedup.md)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        bundle = build_bundle(args.input, args.anchor)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output_path = args.output or default_output_path(args.input)
    write_markdown(bundle, output_path)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
