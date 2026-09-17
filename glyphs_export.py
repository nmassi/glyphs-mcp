"""Export saved Glyphs sources into timestamped, organized directories."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SOURCE_SUFFIXES = {".glyphs", ".glyphspackage"}
FORMAT_DIRECTORIES = {
    ".otf": "otf",
    ".ttf": "ttf",
    ".woff": "woff",
    ".woff2": "woff2",
}
EXPORT_RUNS = (
    ("static-cff", "cff", ["standard"], False),
    ("truetype", "tt", ["standard"], True),
    ("static-web", "tt", ["woff", "woff2"], False),
)


def format_export_log(result: dict) -> str:
    """Build the user-facing log returned by both the CLI and MCP tool."""
    lines = ["## Glyphs export log", f"Status: {'SUCCESS' if result.get('ok') else 'FAILED'}"]
    if result.get("sourcePath"):
        lines.append(f"Source: {result['sourcePath']}")
    if result.get("outputDirectory"):
        lines.append(f"Output: {result['outputDirectory']}")

    exported_files = result.get("exportedFiles", [])
    lines.append(f"Exported files: {len(exported_files)}")
    lines.extend(f"- {path}" for path in exported_files)

    warnings = result.get("warnings", [])
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)

    errors = list(result.get("errors", []))
    if result.get("error"):
        errors.insert(0, result["error"])
    if errors:
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in dict.fromkeys(errors) if error)

    if result.get("reportPath"):
        lines.append(f"Report: {result['reportPath']}")
    return "\n".join(lines)


def _with_export_log(result: dict) -> dict:
    result["exportLog"] = format_export_log(result)
    return result


def find_glyphs_cli() -> str | None:
    """Find the glyphs-cli executable, preferring this tool's environment."""
    candidates = [
        Path(sys.executable).parent / "glyphs",
        Path.home() / ".local/bin/glyphs",
        Path("/opt/homebrew/bin/glyphs"),
        Path("/usr/local/bin/glyphs"),
    ]
    path_executable = shutil.which("glyphs")
    if path_executable:
        candidates.insert(1, Path(path_executable))

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _validated_source(source: str | os.PathLike[str]) -> Path:
    path = Path(source).expanduser().resolve()
    if path.suffix.lower() not in SOURCE_SUFFIXES:
        raise ValueError("Source must be a .glyphs file or .glyphspackage")
    if not path.exists():
        raise ValueError(f"Source not found: {path}")
    return path


def _timestamped_output(source: Path, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now().astimezone()).strftime("%Y-%m-%d_%H-%M-%S")
    base = source.parent / "export"
    output = base / stamp
    suffix = 2
    while output.exists():
        output = base / f"{stamp}_{suffix}"
        suffix += 1
    return output


def _export_config(
    source: Path,
    temporary_directory: Path,
    outline: str,
    containers: list[str],
    include_variable: bool,
) -> dict:
    source_config: dict[str, object] = {"filePath": str(source)}
    if not include_variable:
        source_config["instances"] = [{"selector": {"type": "single"}}]
    return {
        "settings": {
            "outputPath": str(temporary_directory),
            "outlineFormats": [outline],
            "containerFormats": containers,
        },
        "sourceFiles": [source_config],
    }


def _problem_text(problem: object) -> str:
    if isinstance(problem, dict):
        parts = [problem.get("title"), problem.get("description")]
        if not any(parts):
            parts.append(problem.get("instancePath"))
        return ": ".join(str(part) for part in parts if part)
    return str(problem)


def _collect_report(
    report_path: Path,
    output_directory: Path,
    final_report,
    exported_files: list[str],
    warnings: list[str],
    errors: list[str],
) -> None:
    if not report_path.exists():
        errors.append(f"glyphs-cli did not create report: {report_path.name}")
        return

    for raw_line in report_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            report = json.loads(raw_line)
        except json.JSONDecodeError as error:
            errors.append(f"Invalid glyphs-cli report: {error}")
            continue

        report_warnings = [_problem_text(item) for item in report.get("warnings", [])]
        report_errors = [_problem_text(item) for item in report.get("errors", [])]
        warnings.extend(report_warnings)
        errors.extend(report_errors)
        exported_path = report.get("exportFilePath")
        if exported_path:
            generated = Path(exported_path)
            suffix = generated.suffix.lower()
            is_variable = report.get("instanceType") == "variable"
            directory_name = "variable" if is_variable and suffix == ".ttf" else FORMAT_DIRECTORIES.get(suffix)

            if is_variable and suffix != ".ttf":
                generated.unlink(missing_ok=True)
                report["exportFilePath"] = None
            elif directory_name and generated.exists():
                target_directory = output_directory / directory_name
                target_directory.mkdir(parents=True, exist_ok=True)
                target = target_directory / generated.name
                if target.exists():
                    errors.append(f"Duplicate export filename: {target}")
                else:
                    shutil.move(str(generated), str(target))
                    report["exportFilePath"] = str(target)
                    exported_files.append(str(target))
            elif directory_name and not report_errors:
                errors.append(f"Exported file is missing: {generated}")

        final_report.write(json.dumps(report, ensure_ascii=False) + "\n")


def export_source(
    source: str | os.PathLike[str],
    *,
    app: str = "",
    plugins: str = "",
    timeout: int = 300,
    glyphs_executable: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Export all supported formats for one saved Glyphs source."""
    try:
        source_path = _validated_source(source)
    except ValueError as error:
        return _with_export_log({
            "ok": False,
            "sourcePath": str(Path(source).expanduser()),
            "error": str(error),
        })

    glyphs_bin = glyphs_executable or find_glyphs_cli()
    if not glyphs_bin:
        return _with_export_log({
            "ok": False,
            "sourcePath": str(source_path),
            "error": "glyphs-cli executable `glyphs` was not found. Install glyphs-cli>=0.6.2.",
        })

    output_directory = _timestamped_output(source_path, now)
    temporary_directory = output_directory / ".glyphs-cli-tmp"
    report_file = output_directory / "export-report.jsonl"
    output_directory.mkdir(parents=True)
    temporary_directory.mkdir()

    exported_files: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    commands: list[list[str]] = []

    try:
        with report_file.open("w", encoding="utf-8") as final_report:
            for index, (label, outline, containers, include_variable) in enumerate(EXPORT_RUNS, 1):
                config_path = temporary_directory / f"config-{index}.json"
                run_report = temporary_directory / f"report-{index}.jsonl"
                config_path.write_text(
                    json.dumps(
                        _export_config(
                            source_path,
                            temporary_directory,
                            outline,
                            containers,
                            include_variable,
                        ),
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                command = [glyphs_bin, "export"]
                if app:
                    command.extend(["--app", app])
                command.extend([
                    "--config",
                    str(config_path),
                    "--plugins",
                    plugins,
                    "--quiet",
                    "--json-output",
                    str(run_report),
                ])
                commands.append(command)

                try:
                    completed = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        timeout=max(1, int(timeout)),
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    errors.append(f"{label} export timed out after {timeout} seconds")
                    continue
                except OSError as error:
                    errors.append(f"Could not run glyphs-cli: {error}")
                    continue

                _collect_report(
                    run_report,
                    output_directory,
                    final_report,
                    exported_files,
                    warnings,
                    errors,
                )
                if completed.returncode:
                    detail = (completed.stderr or completed.stdout or "").strip()
                    errors.append(
                        f"{label} export exited with {completed.returncode}"
                        + (f": {detail[-1200:]}" if detail else "")
                    )
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)

    unique_warnings = list(dict.fromkeys(item for item in warnings if item))
    unique_errors = list(dict.fromkeys(item for item in errors if item))
    return _with_export_log({
        "ok": bool(exported_files) and not unique_errors,
        "sourcePath": str(source_path),
        "outputDirectory": str(output_directory),
        "reportPath": str(report_file),
        "exportedFiles": exported_files,
        "warnings": unique_warnings,
        "errors": unique_errors,
        "commands": commands,
    })


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="export-glyphs",
        description="Export OTF, TTF, WOFF, WOFF2, and variable fonts into timestamped folders.",
    )
    parser.add_argument("sources", nargs="+", help=".glyphs files or .glyphspackage directories")
    parser.add_argument("--app", default="", help="Glyphs app selector or application path")
    parser.add_argument("--plugins", default="", help="Plug-in selectors passed to glyphs-cli")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout per export run in seconds")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    results = [
        export_source(source, app=args.app, plugins=args.plugins, timeout=args.timeout)
        for source in args.sources
    ]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("\n\n".join(result["exportLog"] for result in results))
    return 0 if all(result.get("ok") for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
