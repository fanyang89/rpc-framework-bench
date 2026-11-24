#!/usr/bin/env python3
"""Generate publication-quality Benchkit charts for the RPC benchmark report.

All figures are rendered through Benchkit's ``benchkit.lwchart`` pipeline
(``generate_chart_from_single_csv``) with custom dataframe/chart processors for
consistent colorblind-safe branding, readable units, and zero label overlap.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile

# Ensure matplotlib has a writable cache directory and non-interactive backend
# before importing Benchkit / matplotlib / seaborn.
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/rpc-bench-mplconfig")

import matplotlib

matplotlib.use("Agg")
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
import seaborn as sns

from benchkit.lwchart import generate_chart_from_single_csv

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "results" / "2026-11-21-full-af19148"
DEFAULT_FIGURES_DIR = ROOT / "report" / "figures"

# Consistent human-readable display names and colorblind-safe palette across all charts.
NATIVE_LABELS = {
    "thrift": "Apache Thrift",
    "capnp": "Cap'n Proto",
    "brpc": "brpc (baidu_std)",
    "grpc-lite": "grpc-lite (Zig)",
    "tonic": "Tonic (Rust)",
    "grpc-cpp-callback": "gRPC C++ (callback)",
    "grpc-cpp": "gRPC C++ (sync)",
    "grpc-go": "gRPC-Go",
}

NATIVE_ORDER = [
    "Apache Thrift",
    "Cap'n Proto",
    "brpc (baidu_std)",
    "grpc-lite (Zig)",
    "Tonic (Rust)",
    "gRPC C++ (callback)",
    "gRPC C++ (sync)",
    "gRPC-Go",
]

FRAMEWORK_PALETTE = {
    "Apache Thrift": "#0072B2",       # Deep blue
    "Cap'n Proto": "#009E73",         # Emerald teal
    "brpc (baidu_std)": "#E69F00",    # Amber orange
    "grpc-lite (Zig)": "#D55E00",     # Vermilion (highlighted)
    "Tonic (Rust)": "#CC79A7",        # Orchid magenta
    "gRPC C++ (callback)": "#56B4E9", # Sky blue
    "gRPC C++ (sync)": "#525252",     # Slate charcoal
    "gRPC-Go": "#8C564B",             # Warm brown
}

FRAMEWORK_MARKERS = {
    "Apache Thrift": "s",
    "Cap'n Proto": "D",
    "brpc (baidu_std)": "^",
    "grpc-lite (Zig)": "o",
    "Tonic (Rust)": "v",
    "gRPC C++ (callback)": "P",
    "gRPC C++ (sync)": "X",
    "gRPC-Go": "p",
}

GRPC_EXT_LABELS = {
    "grpc-lite": "grpc-lite\n(Zig)",
    "tonic": "Tonic\n(Rust)",
    "grpc-cpp-callback": "gRPC C++\n(callback)",
    "grpc-go": "gRPC-Go",
    "grpc-cpp-sync": "gRPC C++\n(sync)",
}

GRPC_EXT_SINGLELINE = {
    "grpc-lite": "grpc-lite (Zig)",
    "tonic": "Tonic (Rust)",
    "grpc-cpp-callback": "gRPC C++ (callback)",
    "grpc-go": "gRPC-Go",
    "grpc-cpp-sync": "gRPC C++ (sync)",
}

GRPC_EXT_ORDER = [
    "grpc-lite\n(Zig)",
    "Tonic\n(Rust)",
    "gRPC C++\n(callback)",
    "gRPC-Go",
    "gRPC C++\n(sync)",
]

GRPC_EXT_ORDER_SINGLELINE = [
    "grpc-lite (Zig)",
    "Tonic (Rust)",
    "gRPC C++ (callback)",
    "gRPC-Go",
    "gRPC C++ (sync)",
]

TRANSPORT_PALETTE = {
    "Plaintext": "#3B82F6",
    "TLS": "#D55E00",
}

TLS_BACKEND_LABELS = {
    "a20631f-mbedtls": "a20631f (old mbedTLS)",
    "af19148-mbedtls": "af19148 (mbedTLS 3.6.6)",
    "af19148-openssl": "af19148 (OpenSSL 4.0.3)",
}

TLS_BACKEND_ORDER = [
    "a20631f (old mbedTLS)",
    "af19148 (mbedTLS 3.6.6)",
    "af19148 (OpenSSL 4.0.3)",
]

TLS_BACKEND_PALETTE = {
    "a20631f (old mbedTLS)": "#94A3B8",
    "af19148 (mbedTLS 3.6.6)": "#E69F00",
    "af19148 (OpenSSL 4.0.3)": "#D55E00",
}

FOOTNOTE_HOST = (
    "AMD Ryzen 9 9955HX (16 cores, performance governor, localhost)  |  "
    "Median of 3 × 5 s rounds; bands/whiskers = min–max"
)


def configure_theme() -> None:
    """Apply clean publication theme for Benchkit seaborn/matplotlib output."""
    sns.set_theme(
        style="whitegrid",
        context="notebook",
        rc={
            "font.family": "DejaVu Sans",
            "axes.edgecolor": "#CBD5E1",
            "axes.linewidth": 0.9,
            "axes.titleweight": "bold",
            "axes.labelcolor": "#1E293B",
            "axes.titlesize": 11.5,
            "axes.labelsize": 10.2,
            "xtick.color": "#334155",
            "ytick.color": "#334155",
            "xtick.labelsize": 9.3,
            "ytick.labelsize": 9.3,
            "grid.color": "#E2E8F0",
            "grid.linestyle": "--",
            "grid.linewidth": 0.7,
            "legend.frameon": False,
            "legend.fontsize": 9.2,
            "figure.facecolor": "white",
            "axes.facecolor": "#FCFDFF",
            "savefig.facecolor": "white",
        },
    )


def format_latency_us(value: float, _pos: int | None = None) -> str:
    if value >= 1000:
        ms = value / 1000
        return f"{ms:.0f} ms" if ms >= 10 else f"{ms:g} ms"
    return f"{value:g} µs"


def format_bytes(size: int) -> str:
    if size >= 1_048_576:
        return f"{size // 1_048_576} MiB"
    if size >= 1024:
        return f"{size // 1024} KiB"
    return f"{size} B"


def _patch_handles(labels: list[str], palette: dict[str, str]) -> list[Patch]:
    return [Patch(facecolor=palette[label], edgecolor="none", label=label) for label in labels]


def _line_handles(labels: list[str], palette: dict[str, str], markers: dict[str, str]) -> list[Line2D]:
    return [
        Line2D(
            [0],
            [0],
            color=palette[label],
            marker=markers[label],
            linewidth=2.3,
            markersize=6.8,
            label=label,
        )
        for label in labels
    ]


def _bar_top_y(ax: plt.Axes, bar: matplotlib.patches.Rectangle) -> float:
    """Return the higher of bar height or the top of any vertical error whisker on that bar."""
    cx = bar.get_x() + bar.get_width() / 2
    top = bar.get_height()
    for line in ax.lines:
        xs = line.get_xdata()
        ys = line.get_ydata()
        if len(xs) == 2 and abs(float(xs[0]) - cx) < bar.get_width() * 0.45:
            top = max(top, float(max(ys)))
    return top


def _bar_right_x(ax: plt.Axes, bar: matplotlib.patches.Rectangle) -> float:
    """Return the farther right of bar width or any horizontal error whisker on that bar."""
    cy = bar.get_y() + bar.get_height() / 2
    right = bar.get_width()
    for line in ax.lines:
        xs = line.get_xdata()
        ys = line.get_ydata()
        if len(ys) == 2 and abs(float(ys[0]) - cy) < bar.get_height() * 0.45:
            right = max(right, float(max(xs)))
    return right


def run_benchkit_chart(
    csv_path: Path,
    dest_dir: Path,
    stem: str,
    **kwargs,
) -> Path:
    """Render a chart via Benchkit lwchart and promote to a stable PNG/PDF stem."""
    with tempfile.TemporaryDirectory(prefix="benchkit-fig-") as tmp:
        tmp_path = Path(tmp)
        generate_chart_from_single_csv(
            csv_pathname=csv_path,
            output_dir=tmp_path,
            prefix=f"{stem}-",
            **kwargs,
        )
        pngs = sorted(tmp_path.glob(f"benchkit-{stem}-*.png"))
        pdfs = sorted(tmp_path.glob(f"benchkit-{stem}-*.pdf"))
        if not pngs or not pdfs:
            raise RuntimeError(f"Benchkit did not generate outputs for {stem}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_png = dest_dir / f"{stem}.png"
        out_pdf = dest_dir / f"{stem}.pdf"
        shutil.copy2(pngs[-1], out_png)
        shutil.copy2(pdfs[-1], out_pdf)
        return out_png


def export_extended_csv(dataset: Path) -> Path:
    """Convert extended gRPC JSONL runs into a Benchkit semicolon CSV."""
    ext_dir = dataset / "extended-measured"
    rows = []
    for path in sorted(ext_dir.glob("*.jsonl")):
        if path.name.startswith("host-telemetry"):
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            cfg = r["config"]
            srv = r["label"]
            if srv == "grpc-cpp":
                srv = "grpc-cpp-callback" if cfg.get("cpp_api") == "callback" else "grpc-cpp-sync"
            rows.append(
                {
                    "input/client": "Go Client"
                    if r["client_framework_version"].startswith("grpc-go")
                    else "Tonic Client",
                    "input/server": srv,
                    "input/mode": cfg["mode"],
                    "input/transport": "TLS" if cfg["tls"] else "Plaintext",
                    "input/server_cores": cfg["server_threads"],
                    "input/request_bytes": cfg["req_size"],
                    "rep": r["round"],
                    "output/ops_per_second": r["ops_per_second"],
                    "output/ops_k": r["ops_per_second"] / 1000.0,
                    "output/server_cpu_percent": r["server_cpu"]["utilization_percent"],
                }
            )
    csv_path = ext_dir / "benchkit-extended.csv"
    pd.DataFrame(rows).to_csv(csv_path, sep=";", index=False)
    return csv_path


def export_tls_csv(dataset: Path) -> Path:
    """Convert grpc-lite TLS backend JSONL runs into a Benchkit semicolon CSV."""
    tls_dir = dataset / "tls" / "measured"
    rows = []
    for path in sorted(tls_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            cfg = r["config"]
            mode = cfg["mode"]
            cores = cfg["server_threads"]
            size = cfg["req_size"]
            if mode == "unary" and size == 1024:
                tier = "1 KiB Unary (1 / 4 / 8 Server Cores)"
                workload = f"Unary 1 KiB\n({cores} {'core' if cores == 1 else 'cores'})"
            elif mode == "unary" and size == 65536:
                tier = "64 KiB Unary & Streaming (1 Server Core)"
                workload = "Unary\n64 KiB"
            elif mode == "client-stream":
                tier = "64 KiB Unary & Streaming (1 Server Core)"
                workload = "Client-Stream\n(16×1 KiB)"
            elif mode == "server-stream":
                tier = "64 KiB Unary & Streaming (1 Server Core)"
                workload = "Server-Stream\n(16×1 KiB)"
            else:
                tier = "64 KiB Unary & Streaming (1 Server Core)"
                workload = "Bidi Ping-Pong\n(16×1 KiB)"
            rows.append(
                {
                    "input/client": "Go Client"
                    if r["client_framework_version"].startswith("grpc-go")
                    else "Tonic Client",
                    "input/backend": TLS_BACKEND_LABELS[r["server_version"]],
                    "input/tier": tier,
                    "input/workload": workload,
                    "input/mode": mode,
                    "input/server_cores": cores,
                    "input/request_bytes": size,
                    "rep": r["round"],
                    "output/ops_per_second": r["ops_per_second"],
                    "output/ops_k": r["ops_per_second"] / 1000.0,
                }
            )
    csv_path = tls_dir / "benchkit-tls.csv"
    pd.DataFrame(rows).to_csv(csv_path, sep=";", index=False)
    return csv_path


def _add_footnote(fig: plt.Figure, text: str = FOOTNOTE_HOST, y: float = 0.014) -> None:
    fig.text(
        0.5,
        y,
        text,
        ha="center",
        va="bottom",
        fontsize=8.1,
        color="#64748B",
        style="italic",
    )


def _deconflict_y(
    targets: list[tuple[float, str, str]],
    min_gap: float,
    y_min: float,
    y_max: float,
) -> list[tuple[float, str, str]]:
    """Push overlapping right-margin text labels apart vertically while preserving order."""
    ordered = sorted(targets, key=lambda item: item[0])
    placed = []
    for y_val, label, color in ordered:
        y_pos = max(y_val, y_min)
        if placed and y_pos - placed[-1][0] < min_gap:
            y_pos = placed[-1][0] + min_gap
        placed.append([y_pos, label, color])
    if placed and placed[-1][0] > y_max:
        overflow = placed[-1][0] - y_max
        for item in placed:
            item[0] -= overflow
        for i in range(1, len(placed)):
            if placed[i][0] - placed[i - 1][0] < min_gap:
                placed[i][0] = placed[i - 1][0] + min_gap
    return [(item[0], item[1], item[2]) for item in placed]


def generate_native_charts(native_csv: Path, dest_dir: Path) -> list[Path]:
    """Generate the 6 core native cross-protocol Benchkit charts."""
    outputs = []

    # 1. Single-Request Latency (P50 & P99 across 0 B, 64 B, 1 KiB)
    def prep_latency(dataframe: pd.DataFrame) -> pd.DataFrame:
        sub = dataframe[dataframe["scenario"] == "latency"].copy()
        sub["Framework"] = sub["framework"].map(NATIVE_LABELS)
        sub["Payload"] = sub["request_bytes"].map(format_bytes)
        melted = sub.melt(
            id_vars=["Framework", "Payload", "rep"],
            value_vars=["p50_us", "p99_us"],
            var_name="Metric",
            value_name="Latency (µs)",
        )
        melted["Metric"] = melted["Metric"].map(
            {
                "p50_us": "Median Latency — P50 (µs, lower is better)",
                "p99_us": "Tail Latency — P99 (µs, lower is better)",
            }
        )
        return melted

    def style_latency(chart: sns.FacetGrid) -> sns.FacetGrid:
        chart.figure.set_size_inches(13.2, 6.4)
        chart.figure.set_dpi(200)
        chart.set_titles("{col_name}", size=10.8, weight="bold", pad=6)
        chart.set_axis_labels("Symmetric Request / Response Payload", "Latency (µs)")
        for idx, ax in enumerate(chart.axes.flat):
            ax.grid(axis="y", linestyle="--", alpha=0.55)
            ax.grid(axis="x", visible=False)
            ax.set_ylim(0, 50 if idx == 0 else 74)
            for container in ax.containers:
                bars = list(container)
                if len(bars) >= 3:
                    bar = bars[2]
                    h = bar.get_height()
                    top_y = _bar_top_y(ax, bar)
                    if h > 0:
                        ax.annotate(
                            f"{h:.1f}",
                            (bar.get_x() + bar.get_width() / 2, top_y),
                            xytext=(0, 4),
                            textcoords="offset points",
                            ha="center",
                            va="bottom",
                            fontsize=7.6,
                            color="#1E293B",
                            rotation=90,
                        )
        sns.despine(fig=chart.figure)
        if chart._legend is not None:
            chart._legend.remove()
        chart.figure.legend(
            handles=_patch_handles(NATIVE_ORDER, FRAMEWORK_PALETTE),
            loc="lower center",
            bbox_to_anchor=(0.5, 0.055),
            ncol=4,
            frameon=False,
            fontsize=9.3,
            columnspacing=1.5,
        )
        chart.figure.suptitle(
            "Single-Request Latency Across 8 RPC Frameworks (1 Server Core, C=1, Plaintext)",
            y=0.985,
            va="top",
            fontsize=13.5,
            fontweight="bold",
            color="#0F172A",
        )
        _add_footnote(
            chart.figure,
            f"Labels above 1 KiB bars show median µs  |  {FOOTNOTE_HOST}",
            y=0.012,
        )
        chart.figure.subplots_adjust(left=0.06, right=0.98, bottom=0.23, wspace=0.14)
        return chart

    outputs.append(
        run_benchkit_chart(
            native_csv,
            dest_dir,
            "01-native-latency",
            plot_name="catplot",
            kind="bar",
            x="Payload",
            y="Latency (µs)",
            hue="Framework",
            col="Metric",
            order=["0 B", "64 B", "1 KiB"],
            hue_order=NATIVE_ORDER,
            palette=FRAMEWORK_PALETTE,
            estimator="median",
            errorbar=("pi", 100),
            err_kws={"linewidth": 1.0, "color": "#475569"},
            sharey=False,
            title="",
            process_dataframe=prep_latency,
            process_chart=style_latency,
        )
    )

    # 2. Concurrency Sweep — Throughput (1 core, 1 KiB)
    conc_map = {1: 0, 8: 1, 32: 2, 128: 3}

    def prep_conc(dataframe: pd.DataFrame) -> pd.DataFrame:
        sub = dataframe[dataframe["scenario"] == "concurrency"].copy()
        sub["Framework"] = sub["framework"].map(NATIVE_LABELS)
        sub["conc_idx"] = sub["concurrency"].map(conc_map)
        sub["ops_k"] = sub["ops_per_second"] / 1000.0
        return sub

    def style_conc_ops(chart: plt.Axes) -> plt.Axes:
        ax = chart
        ax.figure.set_size_inches(11.4, 6.2)
        ax.figure.set_dpi(200)
        ax.set_xticks([0, 1, 2, 3])
        ax.set_xticklabels(["C = 1", "C = 8", "C = 32", "C = 128"], fontsize=10)
        ax.set_xlim(-0.12, 3.88)
        ax.set_ylim(0, 155)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.0f}k"))
        ax.grid(axis="y", linestyle="--", alpha=0.55)
        ax.grid(axis="x", linestyle=":", alpha=0.35)
        for line in ax.lines:
            line.set_linewidth(2.3)
            line.set_markersize(6.8)
        raw = pd.read_csv(native_csv, sep=";", comment="#")
        raw.columns = [c.split("/", 1)[-1] for c in raw.columns]
        c128 = (
            raw[(raw["scenario"] == "concurrency") & (raw["concurrency"] == 128)]
            .groupby("framework")["ops_per_second"]
            .median()
            / 1000.0
        )
        targets = [
            (float(c128[fw]), f"{NATIVE_LABELS[fw]}: {c128[fw]:.1f}k", FRAMEWORK_PALETTE[NATIVE_LABELS[fw]])
            for fw in NATIVE_LABELS
        ]
        for y_pos, txt, color in _deconflict_y(targets, min_gap=7.2, y_min=8, y_max=148):
            ax.text(3.08, y_pos, txt, fontsize=8.9, fontweight="bold", color=color, va="center")
        if ax.get_legend() is not None:
            ax.get_legend().remove()
        sns.despine(ax=ax)
        ax.set_title(
            "Single-Core Concurrency Scaling: Throughput (1 KiB, Plaintext)",
            fontsize=13,
            fontweight="bold",
            color="#0F172A",
            pad=12,
        )
        ax.set_xlabel("Concurrent Closed-Loop Clients (1 Connection per Worker)\n", labelpad=8)
        ax.set_ylabel("Throughput (k RPC/s)")
        _add_footnote(ax.figure, FOOTNOTE_HOST, y=0.015)
        return ax

    outputs.append(
        run_benchkit_chart(
            native_csv,
            dest_dir,
            "02-native-concurrency-throughput",
            plot_name="lineplot",
            x="conc_idx",
            y="ops_k",
            hue="Framework",
            style="Framework",
            hue_order=NATIVE_ORDER,
            style_order=NATIVE_ORDER,
            palette=FRAMEWORK_PALETTE,
            markers=FRAMEWORK_MARKERS,
            dashes=False,
            estimator="median",
            errorbar=("pi", 100),
            title="",
            process_dataframe=prep_conc,
            process_chart=style_conc_ops,
        )
    )

    # 3. Concurrency Sweep — P99 Tail Latency (Log Scale)
    def style_conc_p99(chart: plt.Axes) -> plt.Axes:
        ax = chart
        ax.figure.set_size_inches(11.4, 6.2)
        ax.figure.set_dpi(200)
        ax.set_yscale("log")
        ax.set_xticks([0, 1, 2, 3])
        ax.set_xticklabels(["C = 1", "C = 8", "C = 32", "C = 128"], fontsize=10)
        ax.set_xlim(-0.12, 3.92)
        ax.set_ylim(14, 24000)
        ticks = [20, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 20000]
        ax.set_yticks(ticks)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(format_latency_us))
        ax.yaxis.set_minor_formatter(ticker.NullFormatter())
        ax.grid(axis="y", linestyle="--", alpha=0.55)
        ax.grid(axis="x", linestyle=":", alpha=0.35)
        for line in ax.lines:
            line.set_linewidth(2.3)
            line.set_markersize(6.8)
        raw = pd.read_csv(native_csv, sep=";", comment="#")
        raw.columns = [c.split("/", 1)[-1] for c in raw.columns]
        c128 = (
            raw[(raw["scenario"] == "concurrency") & (raw["concurrency"] == 128)]
            .groupby("framework")["p99_us"]
            .median()
        )
        targets = [
            (
                float(np.log10(c128[fw])),
                f"{NATIVE_LABELS[fw]}: {c128[fw] / 1000:.2f} ms",
                FRAMEWORK_PALETTE[NATIVE_LABELS[fw]],
            )
            for fw in NATIVE_LABELS
        ]
        for log_y, txt, color in _deconflict_y(
            targets, min_gap=0.145, y_min=np.log10(250), y_max=np.log10(19000)
        ):
            ax.text(3.08, 10**log_y, txt, fontsize=8.9, fontweight="bold", color=color, va="center")
        if ax.get_legend() is not None:
            ax.get_legend().remove()
        sns.despine(ax=ax)
        ax.set_title(
            "Single-Core Concurrency Scaling: P99 Tail Latency (Log Scale, 1 KiB)",
            fontsize=13,
            fontweight="bold",
            color="#0F172A",
            pad=12,
        )
        ax.set_xlabel("Concurrent Closed-Loop Clients (1 Connection per Worker)\n", labelpad=8)
        ax.set_ylabel("P99 Latency (log scale, lower is better)")
        _add_footnote(ax.figure, FOOTNOTE_HOST, y=0.015)
        return ax

    outputs.append(
        run_benchkit_chart(
            native_csv,
            dest_dir,
            "03-native-concurrency-p99",
            plot_name="lineplot",
            x="conc_idx",
            y="p99_us",
            hue="Framework",
            style="Framework",
            hue_order=NATIVE_ORDER,
            style_order=NATIVE_ORDER,
            palette=FRAMEWORK_PALETTE,
            markers=FRAMEWORK_MARKERS,
            dashes=False,
            estimator="median",
            errorbar=("pi", 100),
            title="",
            process_dataframe=prep_conc,
            process_chart=style_conc_p99,
        )
    )

    # 4. Multicore Scaling (1, 4, 8 cores, C=128, 1 KiB)
    core_map = {1: 0, 4: 1, 8: 2}

    def prep_multicore(dataframe: pd.DataFrame) -> pd.DataFrame:
        sub = dataframe[dataframe["scenario"] == "multicore"].copy()
        sub["Framework"] = sub["framework"].map(NATIVE_LABELS)
        sub["core_idx"] = sub["server_cores"].map(core_map)
        sub["ops_k"] = sub["ops_per_second"] / 1000.0
        return sub

    def style_multicore(chart: plt.Axes) -> plt.Axes:
        ax = chart
        ax.figure.set_size_inches(11.6, 6.4)
        ax.figure.set_dpi(200)
        ax.set_xticks([0, 1, 2])
        ax.set_xticklabels(["1 Server Core", "4 Server Cores", "8 Server Cores"], fontsize=10)
        ax.set_xlim(-0.10, 2.82)
        ax.set_ylim(0, 1080)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.0f}k"))
        ax.grid(axis="y", linestyle="--", alpha=0.55)
        ax.grid(axis="x", linestyle=":", alpha=0.35)
        for line in ax.lines:
            line.set_linewidth(2.4)
            line.set_markersize(7.0)
        raw = pd.read_csv(native_csv, sep=";", comment="#")
        raw.columns = [c.split("/", 1)[-1] for c in raw.columns]
        mc = raw[raw["scenario"] == "multicore"].groupby(["framework", "server_cores"])["ops_per_second"].median()
        targets = []
        for fw in NATIVE_LABELS:
            c1 = mc[(fw, 1)]
            c8 = mc[(fw, 8)]
            ratio = c8 / c1
            suffix = "*" if fw == "grpc-lite" else ""
            targets.append(
                (
                    float(c8 / 1000.0),
                    f"{NATIVE_LABELS[fw]}: {c8 / 1000:.0f}k ({ratio:.1f}×){suffix}",
                    FRAMEWORK_PALETTE[NATIVE_LABELS[fw]],
                )
            )
        for y_pos, txt, color in _deconflict_y(targets, min_gap=46.0, y_min=60, y_max=1040):
            ax.text(2.06, y_pos, txt, fontsize=8.9, fontweight="bold", color=color, va="center")
        if ax.get_legend() is not None:
            ax.get_legend().remove()
        sns.despine(ax=ax)
        ax.set_title(
            "Multicore Scaling Across 8 RPC Frameworks (1 KiB, C=128, Plaintext)",
            fontsize=13,
            fontweight="bold",
            color="#0F172A",
            pad=12,
        )
        ax.set_xlabel("Server CPU Affinity Budget (Clients Pinned to Physical Cores 0–7)\n", labelpad=8)
        ax.set_ylabel("Throughput (k RPC/s)")
        _add_footnote(
            ax.figure,
            "*grpc-lite 8-core client CPU 588% vs server 256% (client-limited)  |  Median of 3 × 5 s rounds; bands = min–max",
            y=0.015,
        )
        return ax

    outputs.append(
        run_benchkit_chart(
            native_csv,
            dest_dir,
            "04-native-multicore-scaling",
            plot_name="lineplot",
            x="core_idx",
            y="ops_k",
            hue="Framework",
            style="Framework",
            hue_order=NATIVE_ORDER,
            style_order=NATIVE_ORDER,
            palette=FRAMEWORK_PALETTE,
            markers=FRAMEWORK_MARKERS,
            dashes=False,
            estimator="median",
            errorbar=("pi", 100),
            title="",
            process_dataframe=prep_multicore,
            process_chart=style_multicore,
        )
    )

    # 5. Large Symmetric Payload Bandwidth (1 KiB, 64 KiB, 1 MiB at C=32, 1 core)
    def prep_payload(dataframe: pd.DataFrame) -> pd.DataFrame:
        sub = dataframe[dataframe["scenario"] == "payload"].copy()
        sub["Framework"] = sub["framework"].map(NATIVE_LABELS)
        sub["Payload"] = sub["request_bytes"].map(format_bytes)
        return sub

    def style_payload(chart: sns.FacetGrid) -> sns.FacetGrid:
        chart.figure.set_size_inches(12.6, 6.4)
        chart.figure.set_dpi(200)
        ax = chart.axes.flat[0]
        ax.set_title("")
        ax.set_xlabel("Symmetric Request & Response Payload Size (Concurrency = 32, 1 Server Core)", labelpad=8)
        ax.set_ylabel("Application Payload Bandwidth (MiB/s, Req + Resp)")
        ax.set_ylim(0, 6650)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.grid(axis="y", linestyle="--", alpha=0.55)
        ax.grid(axis="x", visible=False)
        for container in ax.containers:
            for bar in container:
                h = bar.get_height()
                if h >= 500:
                    top_y = _bar_top_y(ax, bar)
                    ax.annotate(
                        f"{h:,.0f}",
                        (bar.get_x() + bar.get_width() / 2, top_y),
                        xytext=(0, 5),
                        textcoords="offset points",
                        ha="center",
                        va="bottom",
                        fontsize=7.7,
                        color="#1E293B",
                        rotation=90,
                    )
        sns.despine(fig=chart.figure)
        if chart._legend is not None:
            chart._legend.remove()
        chart.figure.legend(
            handles=_patch_handles(NATIVE_ORDER, FRAMEWORK_PALETTE),
            loc="lower center",
            bbox_to_anchor=(0.5, 0.055),
            ncol=4,
            frameon=False,
            fontsize=9.3,
            columnspacing=1.5,
        )
        chart.figure.suptitle(
            "Symmetric Payload Bandwidth: 1 KiB vs 64 KiB vs 1 MiB (1 Server Core, C=32)",
            y=0.98,
            va="top",
            fontsize=13.5,
            fontweight="bold",
            color="#0F172A",
        )
        _add_footnote(chart.figure, FOOTNOTE_HOST, y=0.012)
        chart.figure.subplots_adjust(left=0.08, right=0.98, bottom=0.22)
        return chart

    outputs.append(
        run_benchkit_chart(
            native_csv,
            dest_dir,
            "05-native-payload-bandwidth",
            plot_name="catplot",
            kind="bar",
            x="Payload",
            y="payload_mib_s",
            hue="Framework",
            order=["1 KiB", "64 KiB", "1 MiB"],
            hue_order=NATIVE_ORDER,
            palette=FRAMEWORK_PALETTE,
            estimator="median",
            errorbar=("pi", 100),
            err_kws={"linewidth": 1.0, "color": "#475569"},
            title="",
            process_dataframe=prep_payload,
            process_chart=style_payload,
        )
    )

    # 6. Asymmetric Payloads (64 B -> 64 KiB vs 64 KiB -> 64 B)
    def prep_asym(dataframe: pd.DataFrame) -> pd.DataFrame:
        sub = dataframe[dataframe["scenario"] == "asymmetric"].copy()
        sub["Framework"] = sub["framework"].map(NATIVE_LABELS)
        sub["Direction"] = np.where(
            sub["request_bytes"] < sub["response_bytes"],
            "Small Req → Large Resp (64 B → 64 KiB)",
            "Large Req → Small Resp (64 KiB → 64 B)",
        )
        sub["ops_k"] = sub["ops_per_second"] / 1000.0
        return sub

    def style_asym(chart: sns.FacetGrid) -> sns.FacetGrid:
        chart.figure.set_size_inches(13.4, 6.2)
        chart.figure.set_dpi(200)
        chart.set_titles("{col_name}", size=10.8, weight="bold", pad=6)
        chart.set_axis_labels("Throughput (k RPC/s)", "")
        for ax in chart.axes.flat:
            ax.set_xlim(0, 108)
            ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.0f}k"))
            ax.grid(axis="x", linestyle="--", alpha=0.55)
            ax.grid(axis="y", visible=False)
            for container in ax.containers:
                for bar in container:
                    w = bar.get_width()
                    if w > 0:
                        right_x = _bar_right_x(ax, bar)
                        mib = w * 1000.0 * (64 + 65536) / 1_048_576
                        ax.annotate(
                            f"{w:.1f}k ({mib:,.0f} MiB/s)",
                            (right_x, bar.get_y() + bar.get_height() / 2),
                            xytext=(5, 0),
                            textcoords="offset points",
                            ha="left",
                            va="center",
                            fontsize=8.2,
                            color="#1E293B",
                        )
        sns.despine(fig=chart.figure)
        chart.figure.suptitle(
            "Asymmetric Payload Throughput: Request vs Response Direction (1 Server Core, C=32)",
            y=0.985,
            va="top",
            fontsize=13.5,
            fontweight="bold",
            color="#0F172A",
        )
        _add_footnote(chart.figure, FOOTNOTE_HOST, y=0.015)
        chart.figure.subplots_adjust(left=0.145, right=0.98, bottom=0.15, wspace=0.10)
        return chart

    outputs.append(
        run_benchkit_chart(
            native_csv,
            dest_dir,
            "06-native-asymmetric-payload",
            plot_name="catplot",
            kind="bar",
            y="Framework",
            x="ops_k",
            hue="Framework",
            col="Direction",
            order=NATIVE_ORDER,
            hue_order=NATIVE_ORDER,
            palette=FRAMEWORK_PALETTE,
            dodge=False,
            legend=False,
            estimator="median",
            errorbar=("pi", 100),
            err_kws={"linewidth": 1.1, "color": "#475569"},
            title="",
            process_dataframe=prep_asym,
            process_chart=style_asym,
        )
    )

    return outputs


def generate_extended_charts(ext_csv: Path, dest_dir: Path) -> list[Path]:
    """Generate the 4 extended gRPC matrix Benchkit charts (Plaintext vs TLS)."""
    outputs = []

    # 7. 1-Core 1 KiB Unary: Plaintext vs TLS (Go Client vs Tonic Client)
    def prep_unary1c(dataframe: pd.DataFrame) -> pd.DataFrame:
        sub = dataframe[
            (dataframe["mode"] == "unary")
            & (dataframe["server_cores"] == 1)
            & (dataframe["request_bytes"] == 1024)
        ].copy()
        sub["Server"] = sub["server"].map(GRPC_EXT_LABELS)
        return sub

    def style_unary1c(chart: sns.FacetGrid) -> sns.FacetGrid:
        chart.figure.set_size_inches(12.8, 6.2)
        chart.figure.set_dpi(200)
        chart.set_titles("{col_name}", size=11.0, weight="bold", pad=6)
        chart.set_axis_labels("", "Throughput (k RPC/s)")
        for ax in chart.axes.flat:
            ax.set_ylim(0, 505)
            ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.0f}k"))
            ax.grid(axis="y", linestyle="--", alpha=0.55)
            ax.grid(axis="x", visible=False)
            for container in ax.containers:
                for bar in container:
                    h = bar.get_height()
                    if h > 0:
                        top_y = _bar_top_y(ax, bar)
                        ax.annotate(
                            f"{h:.1f}k",
                            (bar.get_x() + bar.get_width() / 2, top_y),
                            xytext=(0, 4),
                            textcoords="offset points",
                            ha="center",
                            va="bottom",
                            fontsize=8.3,
                            fontweight="bold",
                            color="#1E293B",
                        )
        sns.despine(fig=chart.figure)
        if chart._legend is not None:
            chart._legend.remove()
        chart.figure.legend(
            handles=_patch_handles(["Plaintext", "TLS"], TRANSPORT_PALETTE),
            loc="lower center",
            bbox_to_anchor=(0.5, 0.055),
            ncol=2,
            frameon=False,
            fontsize=10.0,
            columnspacing=2.2,
        )
        chart.figure.suptitle(
            "gRPC 1-Core Unary (1 KiB): Plaintext vs Certificate-Verified TLS (C=512, 32 Conns)",
            y=0.985,
            va="top",
            fontsize=13.2,
            fontweight="bold",
            color="#0F172A",
        )
        _add_footnote(
            chart.figure,
            f"grpc-lite af19148 uses OpenSSL 4.0.3 for TLS  |  {FOOTNOTE_HOST}",
            y=0.012,
        )
        chart.figure.subplots_adjust(left=0.07, right=0.98, bottom=0.21, wspace=0.12)
        return chart

    outputs.append(
        run_benchkit_chart(
            ext_csv,
            dest_dir,
            "07-grpc-unary-plaintext-vs-tls",
            plot_name="catplot",
            kind="bar",
            x="Server",
            y="ops_k",
            hue="transport",
            col="client",
            order=GRPC_EXT_ORDER,
            hue_order=["Plaintext", "TLS"],
            palette=TRANSPORT_PALETTE,
            estimator="median",
            errorbar=("pi", 100),
            err_kws={"linewidth": 1.1, "color": "#475569"},
            title="",
            process_dataframe=prep_unary1c,
            process_chart=style_unary1c,
        )
    )

    # 8. gRPC Multicore Scaling (1 / 4 / 8 cores, Plaintext vs TLS, Go vs Tonic)
    def prep_ext_mc(dataframe: pd.DataFrame) -> pd.DataFrame:
        sub = dataframe[
            (dataframe["mode"] == "unary") & (dataframe["request_bytes"] == 1024)
        ].copy()
        sub["Server"] = sub["server"].map(GRPC_EXT_SINGLELINE)
        sub["Cores"] = sub["server_cores"].map({1: "1 Core", 4: "4 Cores", 8: "8 Cores"})
        return sub

    def style_ext_mc(chart: sns.FacetGrid) -> sns.FacetGrid:
        chart.figure.set_size_inches(12.6, 8.4)
        chart.figure.set_dpi(200)
        chart.set_titles("{row_name} — {col_name}", size=10.8, weight="bold", pad=6)
        chart.set_axis_labels("Server CPU Affinity Budget", "Throughput (k RPC/s)")
        for ax in chart.axes.flat:
            ax.set_ylim(0, 745)
            ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.0f}k"))
            ax.grid(axis="y", linestyle="--", alpha=0.55)
            ax.grid(axis="x", linestyle=":", alpha=0.35)
        sns.despine(fig=chart.figure)
        if chart._legend is not None:
            chart._legend.remove()
        chart.figure.legend(
            handles=_line_handles(GRPC_EXT_ORDER_SINGLELINE, FRAMEWORK_PALETTE, FRAMEWORK_MARKERS),
            loc="lower center",
            bbox_to_anchor=(0.5, 0.048),
            ncol=5,
            frameon=False,
            fontsize=9.3,
            columnspacing=1.2,
        )
        chart.figure.suptitle(
            "gRPC Multicore Unary Scaling: Plaintext & TLS Across Go and Tonic Clients (1 KiB, C=512)",
            y=0.985,
            va="top",
            fontsize=13.2,
            fontweight="bold",
            color="#0F172A",
        )
        _add_footnote(
            chart.figure,
            "grpc-lite 4–8 core plateau is client-CPU limited (server CPU 176%–293% of 400%–800% budget)  |  Median of 3 × 5 s rounds; whiskers = min–max",
            y=0.012,
        )
        chart.figure.subplots_adjust(left=0.07, right=0.98, bottom=0.155, hspace=0.28, wspace=0.12)
        return chart

    outputs.append(
        run_benchkit_chart(
            ext_csv,
            dest_dir,
            "08-grpc-multicore-plaintext-vs-tls",
            plot_name="catplot",
            kind="point",
            x="Cores",
            y="ops_k",
            hue="Server",
            row="transport",
            col="client",
            order=["1 Core", "4 Cores", "8 Cores"],
            row_order=["Plaintext", "TLS"],
            col_order=["Go Client", "Tonic Client"],
            hue_order=GRPC_EXT_ORDER_SINGLELINE,
            palette=FRAMEWORK_PALETTE,
            markers=[FRAMEWORK_MARKERS[s] for s in GRPC_EXT_ORDER_SINGLELINE],
            estimator="median",
            errorbar=("pi", 100),
            dodge=0.08,
            title="",
            process_dataframe=prep_ext_mc,
            process_chart=style_ext_mc,
        )
    )

    # 9. gRPC Streaming Matrix (Client-Stream, Server-Stream, Bidi Ping-Pong)
    mode_titles = {
        "client-stream": "Client-Stream (16 req → 1 resp)",
        "server-stream": "Server-Stream (1 req → 16 resp)",
        "bidi": "Bidi Ping-Pong (16 exchanges)",
    }

    def prep_stream(dataframe: pd.DataFrame) -> pd.DataFrame:
        sub = dataframe[dataframe["mode"].isin(mode_titles)].copy()
        sub["Server"] = sub["server"].map(GRPC_EXT_LABELS)
        sub["Mode"] = sub["mode"].map(mode_titles)
        return sub

    def style_stream(chart: sns.FacetGrid) -> sns.FacetGrid:
        chart.figure.set_size_inches(14.8, 8.0)
        chart.figure.set_dpi(200)
        chart.set_titles("{row_name} | {col_name}", size=10.2, weight="bold", pad=6)
        chart.set_axis_labels("", "Complete Streaming RPCs / s (k)")
        col_ymax = [78, 58, 11.2]
        for r_idx in range(2):
            for c_idx in range(3):
                ax = chart.axes[r_idx, c_idx]
                ax.set_ylim(0, col_ymax[c_idx])
                ax.yaxis.set_major_formatter(
                    ticker.FuncFormatter(
                        lambda v, _, c=c_idx: f"{v:.0f}k" if c < 2 else f"{v:.1f}k"
                    )
                )
                ax.grid(axis="y", linestyle="--", alpha=0.55)
                ax.grid(axis="x", visible=False)
                for container in ax.containers:
                    for bar in container:
                        h = bar.get_height()
                        if h > 0:
                            top_y = _bar_top_y(ax, bar)
                            fmt = f"{h:.1f}" if c_idx == 2 else f"{h:.0f}"
                            ax.annotate(
                                fmt,
                                (bar.get_x() + bar.get_width() / 2, top_y),
                                xytext=(0, 3),
                                textcoords="offset points",
                                ha="center",
                                va="bottom",
                                fontsize=7.9,
                                fontweight="bold",
                                color="#1E293B",
                            )
        sns.despine(fig=chart.figure)
        if chart._legend is not None:
            chart._legend.remove()
        chart.figure.legend(
            handles=_patch_handles(["Plaintext", "TLS"], TRANSPORT_PALETTE),
            loc="lower center",
            bbox_to_anchor=(0.5, 0.045),
            ncol=2,
            frameon=False,
            fontsize=10.2,
            columnspacing=2.2,
        )
        chart.figure.suptitle(
            "gRPC Streaming Performance: Plaintext vs TLS (1 Server Core, 16 × 1 KiB Messages/RPC, C=32)",
            y=0.985,
            va="top",
            fontsize=13.2,
            fontweight="bold",
            color="#0F172A",
        )
        _add_footnote(
            chart.figure,
            f"1 operation = complete streaming RPC (16 messages + half-close + OK status)  |  {FOOTNOTE_HOST}",
            y=0.012,
        )
        chart.figure.subplots_adjust(left=0.055, right=0.985, bottom=0.16, hspace=0.33, wspace=0.15)
        return chart

    outputs.append(
        run_benchkit_chart(
            ext_csv,
            dest_dir,
            "09-grpc-streaming-plaintext-vs-tls",
            plot_name="catplot",
            kind="bar",
            x="Server",
            y="ops_k",
            hue="transport",
            row="client",
            col="Mode",
            order=GRPC_EXT_ORDER,
            hue_order=["Plaintext", "TLS"],
            row_order=["Go Client", "Tonic Client"],
            col_order=list(mode_titles.values()),
            palette=TRANSPORT_PALETTE,
            sharey=False,
            estimator="median",
            errorbar=("pi", 100),
            err_kws={"linewidth": 1.0, "color": "#475569"},
            title="",
            process_dataframe=prep_stream,
            process_chart=style_stream,
        )
    )

    # 10. 64 KiB Unary: Plaintext vs TLS (1 Core)
    def prep_64k(dataframe: pd.DataFrame) -> pd.DataFrame:
        sub = dataframe[
            (dataframe["mode"] == "unary") & (dataframe["request_bytes"] == 65536)
        ].copy()
        sub["Server"] = sub["server"].map(GRPC_EXT_LABELS)
        return sub

    def style_64k(chart: sns.FacetGrid) -> sns.FacetGrid:
        chart.figure.set_size_inches(12.8, 6.2)
        chart.figure.set_dpi(200)
        chart.set_titles("{col_name}", size=11.0, weight="bold", pad=6)
        chart.set_axis_labels("", "Throughput (k RPC/s)")
        for ax in chart.axes.flat:
            ax.set_ylim(0, 34)
            ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.0f}k"))
            ax.grid(axis="y", linestyle="--", alpha=0.55)
            ax.grid(axis="x", visible=False)
            for container in ax.containers:
                for bar in container:
                    h = bar.get_height()
                    if h > 0:
                        top_y = _bar_top_y(ax, bar)
                        ax.annotate(
                            f"{h:.1f}k",
                            (bar.get_x() + bar.get_width() / 2, top_y),
                            xytext=(0, 4),
                            textcoords="offset points",
                            ha="center",
                            va="bottom",
                            fontsize=8.3,
                            fontweight="bold",
                            color="#1E293B",
                        )
        sns.despine(fig=chart.figure)
        if chart._legend is not None:
            chart._legend.remove()
        chart.figure.legend(
            handles=_patch_handles(["Plaintext", "TLS"], TRANSPORT_PALETTE),
            loc="lower center",
            bbox_to_anchor=(0.5, 0.055),
            ncol=2,
            frameon=False,
            fontsize=10.0,
            columnspacing=2.2,
        )
        chart.figure.suptitle(
            "gRPC Large-Payload Unary (64 KiB): Plaintext vs TLS (1 Server Core, C=512, 32 Conns)",
            y=0.985,
            va="top",
            fontsize=13.2,
            fontweight="bold",
            color="#0F172A",
        )
        _add_footnote(chart.figure, FOOTNOTE_HOST, y=0.012)
        chart.figure.subplots_adjust(left=0.07, right=0.98, bottom=0.21, wspace=0.12)
        return chart

    outputs.append(
        run_benchkit_chart(
            ext_csv,
            dest_dir,
            "10-grpc-large-64kib-plaintext-vs-tls",
            plot_name="catplot",
            kind="bar",
            x="Server",
            y="ops_k",
            hue="transport",
            col="client",
            order=GRPC_EXT_ORDER,
            hue_order=["Plaintext", "TLS"],
            palette=TRANSPORT_PALETTE,
            estimator="median",
            errorbar=("pi", 100),
            err_kws={"linewidth": 1.1, "color": "#475569"},
            title="",
            process_dataframe=prep_64k,
            process_chart=style_64k,
        )
    )

    return outputs


def generate_tls_evolution_chart(tls_csv: Path, dest_dir: Path) -> Path:
    """Generate the grpc-lite TLS backend evolution chart (a20631f -> af19148 OpenSSL)."""
    raw = pd.read_csv(tls_csv, sep=";")
    raw.columns = [c.split("/", 1)[-1] for c in raw.columns]
    med = raw.groupby(["client", "tier", "workload", "backend"])["ops_k"].median()

    tier_orders = {
        "1 KiB Unary (1 / 4 / 8 Server Cores)": [
            "Unary 1 KiB\n(1 core)",
            "Unary 1 KiB\n(4 cores)",
            "Unary 1 KiB\n(8 cores)",
        ],
        "64 KiB Unary & Streaming (1 Server Core)": [
            "Client-Stream\n(16×1 KiB)",
            "Server-Stream\n(16×1 KiB)",
            "Unary\n64 KiB",
            "Bidi Ping-Pong\n(16×1 KiB)",
        ],
    }

    def prep_tls(dataframe: pd.DataFrame) -> pd.DataFrame:
        return dataframe.copy()

    def style_tls(chart: sns.FacetGrid) -> sns.FacetGrid:
        chart.figure.set_size_inches(13.4, 8.4)
        chart.figure.set_dpi(200)
        tiers = list(tier_orders.keys())
        clients = ["Go Client", "Tonic Client"]
        row_ymax = [680, 58]
        for r_idx, tier in enumerate(tiers):
            w_order = tier_orders[tier]
            for c_idx, client in enumerate(clients):
                ax = chart.axes[r_idx, c_idx]
                ax.clear()
                sub = raw[(raw["tier"] == tier) & (raw["client"] == client)]
                sns.barplot(
                    data=sub,
                    x="workload",
                    y="ops_k",
                    hue="backend",
                    order=w_order,
                    hue_order=TLS_BACKEND_ORDER,
                    palette=TLS_BACKEND_PALETTE,
                    estimator="median",
                    errorbar=("pi", 100),
                    err_kws={"linewidth": 1.0, "color": "#475569"},
                    ax=ax,
                )
                ax.set_title(f"{client} — {tier}", size=10.8, weight="bold", pad=6)
                ax.set_xlabel("")
                ax.set_ylabel("Throughput (k RPC/s)" if c_idx == 0 else "")
                ax.set_ylim(0, row_ymax[r_idx])
                ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.0f}k"))
                ax.grid(axis="y", linestyle="--", alpha=0.55)
                ax.grid(axis="x", visible=False)
                if ax.get_legend() is not None:
                    ax.get_legend().remove()
                for b_idx, container in enumerate(ax.containers):
                    for w_idx, bar in enumerate(container):
                        h = bar.get_height()
                        if h <= 0:
                            continue
                        wl = w_order[w_idx]
                        top_y = _bar_top_y(ax, bar)
                        label = f"{h:.0f}k" if r_idx == 0 else f"{h:.1f}k"
                        if b_idx == 2:
                            old_v = med[(client, tier, wl, TLS_BACKEND_ORDER[0])]
                            ratio = h / old_v
                            ax.annotate(
                                f"{label}\n({ratio:.1f}×)",
                                (bar.get_x() + bar.get_width() / 2, top_y),
                                xytext=(0, 3),
                                textcoords="offset points",
                                ha="center",
                                va="bottom",
                                fontsize=8.0,
                                fontweight="bold",
                                color="#9A3412",
                            )
                        else:
                            ax.annotate(
                                label,
                                (bar.get_x() + bar.get_width() / 2, top_y),
                                xytext=(0, 3),
                                textcoords="offset points",
                                ha="center",
                                va="bottom",
                                fontsize=7.6,
                                color="#475569",
                            )
        sns.despine(fig=chart.figure)
        if chart._legend is not None:
            chart._legend.remove()
        chart.figure.legend(
            handles=_patch_handles(TLS_BACKEND_ORDER, TLS_BACKEND_PALETTE),
            loc="lower center",
            bbox_to_anchor=(0.5, 0.048),
            ncol=3,
            frameon=False,
            fontsize=9.8,
            columnspacing=1.8,
        )
        chart.figure.suptitle(
            "grpc-lite TLS Evolution: Historical mbedTLS → af19148 mbedTLS → af19148 OpenSSL 4.0.3",
            y=0.985,
            va="top",
            fontsize=13.2,
            fontweight="bold",
            color="#0F172A",
        )
        _add_footnote(
            chart.figure,
            "Parentheses = af19148 OpenSSL 4.0.3 ÷ historical a20631f mbedTLS  |  Median of 3 × 5 s rounds; whiskers = min–max",
            y=0.012,
        )
        chart.figure.subplots_adjust(left=0.07, right=0.98, bottom=0.155, hspace=0.35, wspace=0.12)
        return chart

    return run_benchkit_chart(
        tls_csv,
        dest_dir,
        "11-grpc-lite-tls-evolution",
        plot_name="catplot",
        kind="bar",
        x="workload",
        y="ops_k",
        hue="backend",
        row="tier",
        col="client",
        row_order=list(tier_orders.keys()),
        col_order=["Go Client", "Tonic Client"],
        hue_order=TLS_BACKEND_ORDER,
        palette=TLS_BACKEND_PALETTE,
        sharex=False,
        sharey=False,
        title="",
        process_dataframe=prep_tls,
        process_chart=style_tls,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="Path to the full benchmark dataset directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_FIGURES_DIR,
        help="Directory where PNG and PDF Benchkit figures will be written.",
    )
    args = parser.parse_args()

    configure_theme()
    dataset = args.dataset.resolve()
    dest = args.output.resolve()

    native_csv = next((dataset / "native-measured").glob("benchmark_*.csv"))
    ext_csv = export_extended_csv(dataset)
    tls_csv = export_tls_csv(dataset)

    generated: list[Path] = []
    generated.extend(generate_native_charts(native_csv, dest))
    generated.extend(generate_extended_charts(ext_csv, dest))
    generated.append(generate_tls_evolution_chart(tls_csv, dest))

    print(f"\nGenerated {len(generated)} Benchkit figure pairs (PNG + PDF) in {dest}:")
    for p in generated:
        print(f"  - {p.name}")


if __name__ == "__main__":
    main()
