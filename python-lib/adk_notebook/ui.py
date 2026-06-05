"""rich rendering toolkit — the single output layer reused by every card file.

The webapp serializes its data to JSON for React; these helpers render the same
data as rich tables / panels / bars / trees in a DSS Jupyter cell. Colour
semantics mirror the app's progress/severity palette:

    grey  = queued / loading / unavailable / neutral-low
    yellow= active / partial / waiting / stalled / WARNING
    white = ready / current / completed-neutral
    green = SUCCESS / OK
    red   = failure / ERROR / FAIL
    cyan  = INFO / accent

Cards stay tiny because all formatting lives here.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from rich.columns import Columns
from rich import box


# Jupyter is auto-detected by rich; in a plain TTY this still renders fine.
console = Console()


# --- severity / tone --------------------------------------------------------
# Maps the app's level vocabulary onto rich styles.
_SEVERITY_STYLES: Dict[str, str] = {
    'ERROR': 'bold red',
    'CRITICAL': 'bold red',
    'FAIL': 'bold red',
    'FAILED': 'bold red',
    'DANGER': 'bold red',
    'WARNING': 'yellow',
    'WARN': 'yellow',
    'PARTIAL': 'yellow',
    'STALLED': 'yellow',
    'INFO': 'cyan',
    'NOTE': 'cyan',
    'SUCCESS': 'bold green',
    'OK': 'green',
    'PASS': 'green',
    'PASSED': 'green',
    'HEALTHY': 'green',
    'NEUTRAL': 'white',
    'UNKNOWN': 'grey62',
    'UNAVAILABLE': 'grey62',
    'QUEUED': 'grey62',
    'LOADING': 'grey62',
}

_SEVERITY_RANK = {
    'ERROR': 4, 'CRITICAL': 4, 'FAIL': 4, 'FAILED': 4, 'DANGER': 4,
    'WARNING': 3, 'WARN': 3, 'PARTIAL': 3, 'STALLED': 3,
    'INFO': 2, 'NOTE': 2,
    'SUCCESS': 1, 'OK': 1, 'PASS': 1, 'PASSED': 1, 'HEALTHY': 1,
}


def severity_style(level: Any) -> str:
    """rich style string for a severity/tone keyword (case-insensitive)."""
    return _SEVERITY_STYLES.get(str(level or '').strip().upper(), 'white')


def severity_rank(level: Any) -> int:
    """Sort key — higher = more severe."""
    return _SEVERITY_RANK.get(str(level or '').strip().upper(), 0)


def badge(level: Any) -> Text:
    """A small coloured ``[LEVEL]`` chip."""
    label = str(level or '').strip().upper() or 'INFO'
    return Text(f" {label} ", style=f"reverse {severity_style(label)}")


# --- primitives -------------------------------------------------------------
def rule(title: str = "", style: str = "cyan") -> None:
    console.rule(Text(title, style=f"bold {style}") if title else "", style=style)


def header(title: str, subtitle: Optional[str] = None) -> None:
    """Section header — a bold rule plus an optional dim subtitle line."""
    console.rule(Text(title, style="bold white"), style="cyan")
    if subtitle:
        console.print(Text(subtitle, style="grey62"), justify="center")
    console.print()


def note(message: str, level: str = "INFO") -> None:
    """One-off status / unavailable line (e.g. 'no data on this host')."""
    console.print(Text(str(message), style=severity_style(level)))


def kv_panel(title: str, mapping: Mapping[str, Any], style: str = "cyan") -> None:
    """A titled panel of aligned key → value rows."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style="grey62", no_wrap=True)
    grid.add_column(justify="left", style="white")
    if not mapping:
        grid.add_row("", Text("(no data)", style="grey62"))
    for key, value in mapping.items():
        if isinstance(value, tuple):
            text, cell_style = value
            grid.add_row(str(key), Text(str(text), style=cell_style))
        else:
            grid.add_row(str(key), Text("" if value is None else str(value)))
    console.print(Panel(grid, title=Text(title, style=f"bold {style}"),
                        border_style=style, box=box.ROUNDED, expand=False))


def _as_cell(value: Any) -> Text:
    """Normalise a cell to rich Text. A (text, style) tuple colours one cell."""
    if isinstance(value, Text):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        text, style = value
        return Text("" if text is None else str(text), style=style or "")
    return Text("" if value is None else str(value))


def data_table(
    title: Optional[str],
    columns: Sequence[Union[str, Mapping[str, Any]]],
    rows: Iterable[Sequence[Any]],
    styles: Optional[Callable[[int, int, Any], Optional[str]]] = None,
    caption: Optional[str] = None,
    zebra: bool = True,
) -> None:
    """Zebra-striped table with optional per-cell colour.

    columns: header strings, or dicts ``{name, justify, style, no_wrap}``.
    rows:    sequences of cells; a cell may be a plain value or a
             ``(value, style)`` tuple to colour that one cell.
    styles:  optional ``fn(row_idx, col_idx, value) -> style|None`` fallback
             colour applied when a cell isn't already a tuple.
    """
    table = Table(
        title=Text(title, style="bold white") if title else None,
        caption=Text(caption, style="grey62") if caption else None,
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        # Zebra striping via `dim` (a foreground attribute) rather than a background
        # colour: a hardcoded background (e.g. "on grey15") looks fine on a dark terminal
        # but paints a near-black box that hides text in a light Jupyter notebook. `dim`
        # has no background, so it stays readable on any page theme.
        row_styles=["", "dim"] if zebra else None,
        expand=False,
        pad_edge=False,
    )
    for col in columns:
        if isinstance(col, Mapping):
            table.add_column(
                str(col.get("name", "")),
                justify=col.get("justify", "left"),
                style=col.get("style"),
                no_wrap=col.get("no_wrap", False),
                max_width=col.get("max_width"),
            )
        else:
            table.add_column(str(col))
    for r_idx, row in enumerate(rows):
        cells: List[Text] = []
        for c_idx, value in enumerate(row):
            if isinstance(value, tuple) and len(value) == 2:
                cells.append(_as_cell(value))
            else:
                fallback = styles(r_idx, c_idx, value) if styles else None
                cells.append(_as_cell((value, fallback)) if fallback else _as_cell(value))
        table.add_row(*cells)
    console.print(table)


_BAR_PALETTE = ["cyan", "green", "magenta", "yellow", "blue", "red", "bright_cyan", "bright_green"]


def bar_list(
    title: Optional[str],
    items: Sequence[Any],
    total: Optional[float] = None,
    width: int = 36,
    value_fmt: Optional[Callable[[float], str]] = None,
    sort: bool = True,
) -> None:
    """Ranked horizontal block-bar list — the text analogue of the app's
    pie / donut / bar charts.

    items: ``(label, value)`` / ``(label, value, style)`` tuples or dicts
           ``{label, value, style}``.
    total: bar scale denominator (defaults to the max value).
    """
    norm: List[Tuple[str, float, Optional[str]]] = []
    for it in items:
        if isinstance(it, Mapping):
            norm.append((str(it.get("label", "")), float(it.get("value") or 0), it.get("style")))
        else:
            label = it[0]
            value = it[1] if len(it) > 1 else 0
            style = it[2] if len(it) > 2 else None
            norm.append((str(label), float(value or 0), style))
    if sort:
        norm.sort(key=lambda x: x[1], reverse=True)

    values = [v for _, v, _ in norm]
    scale = float(total) if total else (max(values) if values else 0.0)
    grand = sum(values)

    grid = Table.grid(padding=(0, 1))
    grid.add_column(justify="right", no_wrap=True, style="white")           # label
    grid.add_column(no_wrap=True)                                            # bar
    grid.add_column(justify="right", no_wrap=True, style="bold white")       # value
    grid.add_column(justify="right", no_wrap=True, style="grey62")           # pct

    if not norm:
        grid.add_row("", Text("(no data)", style="grey62"), "", "")
    for idx, (label, value, style) in enumerate(norm):
        filled = int(round((value / scale) * width)) if scale > 0 else 0
        filled = max(0, min(width, filled))
        bar = Text("█" * filled + "░" * (width - filled), style=style or _BAR_PALETTE[idx % len(_BAR_PALETTE)])
        valstr = value_fmt(value) if value_fmt else f"{value:,.0f}"
        pct = f"{(value / grand * 100):.0f}%" if grand > 0 else ""
        grid.add_row(Text(label, style="white"), bar, valstr, pct)

    if title:
        console.print(Panel(grid, title=Text(title, style="bold cyan"),
                            border_style="cyan", box=box.ROUNDED, expand=False))
    else:
        console.print(grid)


def stat_cards(items: Sequence[Mapping[str, Any]], columns: Optional[int] = None) -> None:
    """A row of summary stat panels (rich Columns of Panels).

    items: dicts ``{label, value, style?, hint?}``.
    """
    panels: List[Panel] = []
    for it in items:
        style = it.get("style") or "cyan"
        body = Text(str(it.get("value", "")), style=f"bold {style}", justify="center")
        if it.get("hint"):
            body = Text.assemble(body, "\n", Text(str(it["hint"]), style="grey62"))
        panels.append(Panel(
            body,
            title=Text(str(it.get("label", "")), style="grey85"),
            border_style=style,
            box=box.ROUNDED,
            expand=True,
        ))
    if not panels:
        console.print(Text("(no stats)", style="grey62"))
        return
    console.print(Columns(panels, equal=True, expand=True,
                          column_first=True, padding=(0, 1)))


def findings(items: Sequence[Mapping[str, Any]], sort: bool = True) -> None:
    """Severity-coloured finding panels with optional remediation.

    items: dicts ``{severity|level, title, detail|description, remediation,
           value|impact}``.
    """
    rows = list(items)
    if sort:
        rows = sorted(rows, key=lambda f: severity_rank(f.get("severity") or f.get("level")), reverse=True)
    if not rows:
        console.print(Panel(Text("No findings.", style="green"),
                            border_style="green", box=box.ROUNDED, expand=False))
        return
    for f in rows:
        level = f.get("severity") or f.get("level") or "INFO"
        style = severity_style(level)
        title = f.get("title") or f.get("name") or f.get("rule") or "Finding"
        body = Text()
        detail = f.get("detail") or f.get("description") or f.get("message")
        if detail:
            body.append(str(detail) + "\n", style="white")
        impact = f.get("value") or f.get("impact") or f.get("savings")
        if impact:
            body.append(f"Impact: {impact}\n", style="bold yellow")
        remediation = f.get("remediation") or f.get("fix") or f.get("recommendation")
        if remediation:
            body.append("→ " + str(remediation), style="grey85")
        console.print(Panel(
            body if str(body) else Text("(no detail)", style="grey62"),
            title=Text.assemble(badge(level), " ", Text(str(title), style=f"bold {style}")),
            border_style=style,
            box=box.ROUNDED,
            expand=False,
        ))


def tree(
    root: Mapping[str, Any],
    label_key: str = "name",
    children_key: str = "children",
    label_fn: Optional[Callable[[Mapping[str, Any]], Any]] = None,
    guide_style: str = "grey50",
) -> None:
    """Render a nested ``{name, children:[...]}`` structure as a rich Tree."""
    def _label(node: Mapping[str, Any]) -> Any:
        if label_fn:
            return label_fn(node)
        return str(node.get(label_key, ""))

    def _attach(parent: Tree, node: Mapping[str, Any]) -> None:
        for child in node.get(children_key) or []:
            branch = parent.add(_label(child))
            _attach(branch, child)

    root_tree = Tree(_label(root), guide_style=guide_style)
    _attach(root_tree, root)
    console.print(root_tree)


def code_block(title: Optional[str], text: str, style: str = "grey85", border: str = "grey50") -> None:
    """Raw multi-line text in a bordered panel — for log excerpts / stack traces."""
    console.print(Panel(
        Text(text, style=style),
        title=Text(title, style="bold white") if title else None,
        border_style=border, box=box.ROUNDED, expand=False,
    ))


def blank() -> None:
    console.print()
