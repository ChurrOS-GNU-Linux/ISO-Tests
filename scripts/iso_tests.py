#!/usr/bin/env python3
"""Static and QEMU smoke tests for authentic ChurrOS ISO images.

Only the Python standard library is required. ISO files are never mounted on
or executed by the host; QEMU is launched without a NIC and with disposable
UEFI variables. See docs/design.md for the evidence and limitations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILES = ROOT / "profiles" / "churros.json"
MIN_ISO_BYTES = 64 * 1024 * 1024
MAX_ISO_BYTES = 16 * 1024 * 1024 * 1024
MAX_COMMAND_OUTPUT = 4 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass
class TestResult:
    name: str
    status: str
    message: str
    duration_seconds: float
    log_path: str | None = None


class Reporter:
    def __init__(self, report_dir: Path, run_id: str):
        self.report_dir = report_dir.resolve()
        self.evidence_dir = self.report_dir / "evidence"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.tests: list[TestResult] = []

    def add(self, name: str, status: str, message: str, started: float,
            log_path: Path | None = None) -> None:
        if status not in {"PASS", "FAIL", "SKIP", "BLOCKED"}:
            raise ValueError(f"Invalid test status: {status}")
        rel_log = None
        if log_path is not None:
            try:
                rel_log = str(log_path.resolve().relative_to(self.report_dir))
            except ValueError:
                rel_log = str(log_path)
        self.tests.append(TestResult(name, status, message,
                                     round(time.monotonic() - started, 3), rel_log))

    def write(self, *, artifact: dict[str, Any], profile: str | None,
              architecture: str | None, modes: list[str], tool_versions: dict[str, str],
              source_url: str | None = None) -> Path:
        result = {
            "schema_version": 1,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "artifact": artifact,
            "source_url": source_url,
            "profile": profile,
            "architecture": architecture,
            "boot_modes_examined": modes,
            "tool_versions": tool_versions,
            "tests": [asdict(t) for t in self.tests],
            "summary": {s: sum(t.status == s for t in self.tests)
                        for s in ("PASS", "FAIL", "SKIP", "BLOCKED")},
        }
        path = self.report_dir / "report.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, path)
        return path

    @property
    def started_at(self) -> str:
        return getattr(self, "_started_at", datetime.now(timezone.utc).isoformat())

    @started_at.setter
    def started_at(self, value: str) -> None:
        self._started_at = value


def safe_source_url(url: str | None) -> str | None:
    """Keep the endpoint identity while dropping potentially secret query/fragment data."""
    if not url:
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        if parsed.port:
            host += f":{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return "[invalid URL omitted]"


def safe_run_id() -> str:
    supplied = os.environ.get("GITHUB_RUN_ID") or os.environ.get("CI_PIPELINE_ID")
    if supplied and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", supplied):
        return supplied
    return f"local-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def load_profiles(path: Path = DEFAULT_PROFILES) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("profile file must be a JSON object with schema_version=1")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("profile file must define a non-empty profiles object")
    for name, p in profiles.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_-]+", name):
            raise ValueError(f"invalid profile name: {name!r}")
        required = {"architecture", "edition", "boot_modes", "qemu", "serial_device",
                    "success_patterns"}
        if not isinstance(p, dict) or not required.issubset(p):
            raise ValueError(f"profile {name!r} is missing required fields")
        if p["architecture"] not in {"x86_64", "aarch64"}:
            raise ValueError(f"profile {name!r}: unsupported architecture {p['architecture']!r}")
        if not isinstance(p["boot_modes"], list) or not p["boot_modes"] or any(
                m not in {"bios", "uefi"} for m in p["boot_modes"]):
            raise ValueError(f"profile {name!r}: boot_modes must contain bios/uefi")
        if p["architecture"] == "aarch64" and "bios" in p["boot_modes"]:
            raise ValueError(f"profile {name!r}: BIOS is not supported for aarch64")
        if not isinstance(p["success_patterns"], list) or not p["success_patterns"]:
            raise ValueError(f"profile {name!r}: success_patterns cannot be empty")
        for pattern in p["success_patterns"]:
            try:
                re.compile(pattern)
            except (TypeError, re.error) as e:
                raise ValueError(f"profile {name!r}: invalid success pattern: {e}") from e
    return data


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_sha256sums(manifest: Path, iso: Path) -> str:
    matches: list[str] = []
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})[ \t]+[* ]?(.+?)", line)
        if match and Path(match.group(2)).name == iso.name:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise ValueError(f"expected exactly one SHA256SUMS entry for {iso.name!r}; found {len(matches)}")
    return matches[0]


def command_env() -> dict[str, str]:
    """Give tools only a small, non-secret environment."""
    keep = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR") if k in os.environ}
    keep.update({"LANG": "C", "LC_ALL": "C"})
    return keep


def run_command(argv: list[str], timeout: float, log_path: Path) -> tuple[int | None, str, str | None]:
    """Run an argv vector without a shell, killing the complete process group on failure."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=command_env(),
                                start_new_session=True)
        try:
            output, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            output, _ = proc.communicate()
            _write_output(log_path, output)
            return None, _decode_output(output), f"command timed out after {timeout:g}s"
        _write_output(log_path, output)
        return proc.returncode, _decode_output(output), None
    except OSError as e:
        text = f"could not execute {argv[0]!r}: {e}"
        log_path.write_text(text + "\n", encoding="utf-8")
        return None, text, text
    except BaseException:
        if proc is not None and proc.poll() is None:
            _kill_process_group(proc)
            proc.communicate()
        raise
    finally:
        _ = started


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def _decode_output(output: bytes) -> str:
    if len(output) > MAX_COMMAND_OUTPUT:
        return output[-MAX_COMMAND_OUTPUT:].decode("utf-8", errors="replace")
    return output.decode("utf-8", errors="replace")


def _write_output(path: Path, output: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(output) > MAX_COMMAND_OUTPUT:
        output = b"[output truncated; tail follows]\n" + output[-MAX_COMMAND_OUTPUT:]
    path.write_bytes(output)


def _tool_version(tool: str, args: list[str] | None = None) -> str:
    path = shutil.which(tool)
    if path is None:
        return "not installed"
    try:
        result = subprocess.run([path, *(args or ["--version"])], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=5, check=False, env=command_env())
        first = result.stdout.decode("utf-8", errors="replace").splitlines()
        return first[0][:240] if first else f"exit {result.returncode}"
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"unavailable ({type(e).__name__})"


def discover_firmware(family: str, code_arg: str | None, vars_arg: str | None) -> tuple[Path, Path] | None:
    if code_arg or vars_arg:
        if not (code_arg and vars_arg):
            raise ValueError("both --uefi-code and --uefi-vars are required")
        code, variables = Path(code_arg), Path(vars_arg)
        if not code.is_file() or not variables.is_file():
            raise ValueError("specified UEFI firmware CODE/VARS file does not exist")
        if code.stat().st_size != variables.stat().st_size:
            raise ValueError("UEFI firmware CODE and VARS files must have equal sizes")
        return code, variables
    if family == "x86_64":
        pairs = [
            ("/usr/share/edk2/x64/OVMF_CODE.4m.fd", "/usr/share/edk2/x64/OVMF_VARS.4m.fd"),
            ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
            ("/usr/share/qemu/OVMF_CODE.fd", "/usr/share/qemu/OVMF_VARS.fd"),
        ]
    elif family == "aarch64":
        pairs = [
            ("/usr/share/AAVMF/AAVMF_CODE.fd", "/usr/share/AAVMF/AAVMF_VARS.fd"),
            ("/usr/share/edk2/aarch64/QEMU_CODE.fd", "/usr/share/edk2/aarch64/QEMU_VARS.fd"),
            ("/usr/share/edk2/aarch64/QEMU_EFI-pflash.raw", "/usr/share/edk2/aarch64/vars-template-pflash.raw"),
        ]
    else:
        raise ValueError(f"unsupported UEFI firmware family: {family}")
    for code_s, vars_s in pairs:
        code, variables = Path(code_s), Path(vars_s)
        if code.is_file() and variables.is_file() and code.stat().st_size == variables.stat().st_size:
            return code, variables
    return None


def validate_iso(iso: Path, profile_name: str, profile: dict[str, Any], reporter: Reporter,
                 expected_sha256: str | None, sums_file: Path | None,
                 xorriso_timeout: float) -> tuple[str | None, dict[str, str]]:
    tool_versions = {"python": sys.version.split()[0], "xorriso": _tool_version("xorriso"),
                     profile.get("qemu", "qemu"): _tool_version(profile.get("qemu", "qemu"))}
    started = time.monotonic()
    if not iso.exists():
        reporter.add("artifact_exists", "FAIL", f"ISO file does not exist: {iso}", started)
        for name in ("artifact_size", "sha256", "iso9660_primary_volume_descriptor", "xorriso_el_torito", "xorriso_system_area", "efi_bootloader_file"):
            reporter.add(name, "SKIP", "No ISO file to inspect", time.monotonic())
        for mode in profile["boot_modes"]:
            reporter.add(f"boot_structure_{mode}", "SKIP", "No ISO file to inspect", time.monotonic())
        return None, tool_versions
    reporter.add("artifact_exists", "PASS", f"Found {iso}", started)

    started = time.monotonic()
    try:
        if not iso.is_file():
            raise ValueError("path is not a regular file")
        size = iso.stat().st_size
        if size == 0:
            raise ValueError("file is empty")
        if size < MIN_ISO_BYTES or size > MAX_ISO_BYTES:
            raise ValueError(f"size {size} bytes is outside accepted range {MIN_ISO_BYTES}..{MAX_ISO_BYTES}")
        reporter.add("artifact_size", "PASS", f"{size} bytes", started)
    except (OSError, ValueError) as e:
        reporter.add("artifact_size", "FAIL", str(e), started)
        size = 0

    started = time.monotonic()
    digest: str | None = None
    try:
        if iso.is_file() and iso.stat().st_size > 0:
            digest = hash_file(iso)
            reporter.add("sha256", "PASS", digest, started)
        else:
            reporter.add("sha256", "SKIP", "No readable non-empty ISO", started)
    except OSError as e:
        reporter.add("sha256", "FAIL", f"cannot hash artifact: {e}", started)

    reference = expected_sha256
    if sums_file is not None:
        try:
            reference = parse_sha256sums(sums_file, iso)
        except (OSError, ValueError) as e:
            reporter.add("published_checksum", "FAIL", str(e), time.monotonic())
            reference = None
    if expected_sha256 is not None and not SHA256_RE.fullmatch(expected_sha256):
        reporter.add("published_checksum", "FAIL", "expected SHA-256 must contain exactly 64 hex characters", time.monotonic())
    elif reference is None:
        if sums_file is None:
            reporter.add("published_checksum", "SKIP", "No trusted checksum reference supplied; SHA-256 is recorded only", time.monotonic())
    elif digest is None:
        reporter.add("published_checksum", "BLOCKED", "Artifact hash could not be computed", time.monotonic())
    elif expected_sha256 and sums_file is None:
        status = "PASS" if digest.lower() == expected_sha256.lower() else "FAIL"
        reporter.add("published_checksum", status,
                     "SHA-256 matches supplied expected value" if status == "PASS" else "SHA-256 does not match supplied expected value",
                     time.monotonic())
    elif reference is not None:
        status = "PASS" if digest.lower() == reference.lower() else "FAIL"
        reporter.add("published_checksum", status,
                     "SHA-256 matches the artifact-specific SHA256SUMS entry" if status == "PASS" else "SHA-256 does not match SHA256SUMS",
                     time.monotonic())

    started = time.monotonic()
    try:
        with iso.open("rb") as f:
            f.seek(16 * 2048 + 1)
            signature = f.read(5)
        if signature == b"CD001":
            reporter.add("iso9660_primary_volume_descriptor", "PASS", "CD001 primary volume descriptor at logical block 16", started)
        else:
            reporter.add("iso9660_primary_volume_descriptor", "FAIL", "missing ISO9660 CD001 primary volume descriptor at logical block 16 (image may be truncated or not ISO)", started)
    except OSError as e:
        reporter.add("iso9660_primary_volume_descriptor", "FAIL", f"cannot read ISO descriptor: {e}", started)

    xorriso = shutil.which("xorriso")
    if not xorriso:
        reporter.add("xorriso_el_torito", "BLOCKED", "xorriso is required to inspect ISO boot catalog", time.monotonic())
        reporter.add("xorriso_system_area", "BLOCKED", "xorriso is required to inspect system area", time.monotonic())
        for mode in profile["boot_modes"]:
            reporter.add(f"boot_structure_{mode}", "BLOCKED", "xorriso inspection unavailable", time.monotonic())
        reporter.add("efi_bootloader_file", "BLOCKED", "xorriso inspection unavailable", time.monotonic())
        return digest, tool_versions

    eltorito_log = reporter.evidence_dir / f"{profile_name}-xorriso-eltorito.log"
    started = time.monotonic()
    rc, output, err = run_command([xorriso, "-indev", str(iso), "-report_el_torito", "plain"], xorriso_timeout, eltorito_log)
    if err or rc != 0:
        reporter.add("xorriso_el_torito", "FAIL" if rc not in (None, 0) or (err and "timed out" in err) else "BLOCKED",
                     err or f"xorriso exited with status {rc}", started, eltorito_log)
        for mode in profile["boot_modes"]:
            reporter.add(f"boot_structure_{mode}", "BLOCKED", "El Torito catalog could not be inspected", time.monotonic(), eltorito_log)
    else:
        modes_found = set()
        for line in output.splitlines():
            if "El Torito boot img" in line:
                if re.search(r"\bBIOS\b", line, re.I):
                    modes_found.add("bios")
                if re.search(r"\bUEFI\b", line, re.I):
                    modes_found.add("uefi")
        if not modes_found:
            reporter.add("xorriso_el_torito", "FAIL", "No El Torito boot image entries found", started, eltorito_log)
            for mode in profile["boot_modes"]:
                reporter.add(f"boot_structure_{mode}", "FAIL", "No El Torito boot image entries found", time.monotonic(), eltorito_log)
        else:
            reporter.add("xorriso_el_torito", "PASS", f"El Torito boot entries found: {', '.join(sorted(modes_found))}", started, eltorito_log)
            for mode in profile["boot_modes"]:
                status = "PASS" if mode in modes_found else "FAIL"
                reporter.add(f"boot_structure_{mode}", status,
                             f"{mode.upper()} El Torito boot entry {'present' if status == 'PASS' else 'missing'}",
                             time.monotonic(), eltorito_log)

    system_log = reporter.evidence_dir / f"{profile_name}-xorriso-system-area.log"
    started = time.monotonic()
    rc, output, err = run_command([xorriso, "-indev", str(iso), "-report_system_area", "plain"], xorriso_timeout, system_log)
    if rc == 0 and err is None:
        reporter.add("xorriso_system_area", "PASS", "xorriso read the system area and partition report", started, system_log)
    else:
        status = "FAIL" if rc not in (None, 0) or (err and "timed out" in err) else "BLOCKED"
        reporter.add("xorriso_system_area", status, err or f"xorriso exited with status {rc}", started, system_log)

    if "uefi" not in profile["boot_modes"]:
        reporter.add("efi_bootloader_file", "SKIP", "Profile does not declare UEFI boot", time.monotonic())
    else:
        loader = profile.get("efi_bootloader")
        if not isinstance(loader, str) or not re.fullmatch(r"BOOT[A-Z0-9]+\.EFI", loader):
            reporter.add("efi_bootloader_file", "FAIL", "profile has an invalid EFI fallback loader name", time.monotonic())
        else:
            efi_log = reporter.evidence_dir / f"{profile_name}-xorriso-efi-files.log"
            started = time.monotonic()
            rc, output, err = run_command([xorriso, "-indev", str(iso), "-find", "/", "-type", "f", "-name", loader, "-exec", "lsdl"], xorriso_timeout, efi_log)
            found = rc == 0 and re.search(rf"(?:^|/)EFI/BOOT/{re.escape(loader)}(?:\s|$)", output, re.I)
            if err or rc != 0:
                status = "FAIL" if rc not in (None, 0) or (err and "timed out" in err) else "BLOCKED"
                reporter.add("efi_bootloader_file", status, err or f"xorriso exited with status {rc}", started, efi_log)
            elif found:
                reporter.add("efi_bootloader_file", "PASS", f"EFI fallback loader /EFI/BOOT/{loader} is present", started, efi_log)
            else:
                reporter.add("efi_bootloader_file", "FAIL", f"EFI fallback loader /EFI/BOOT/{loader} not found in ISO tree", started, efi_log)
    return digest, tool_versions


def _qemu_args(profile: dict[str, Any], mode: str, iso: Path, serial_log: Path,
               firmware: tuple[Path, Path] | None, vars_copy: Path) -> list[str]:
    qemu = shutil.which(profile["qemu"]) or profile["qemu"]
    arch = profile["architecture"]
    common = [qemu, "-no-user-config", "-nodefaults", "-no-reboot", "-no-shutdown",
              "-display", "none", "-monitor", "none", "-serial", f"file:{serial_log}",
              "-nic", "none", "-m", "4096", "-smp", "2"]
    if arch == "x86_64":
        common += ["-machine", "q35,accel=tcg", "-cpu", "max", "-boot", "order=d",
                   "-cdrom", str(iso)]
    elif arch == "aarch64":
        common += ["-machine", "virt,accel=tcg", "-cpu", "cortex-a72", "-boot", "order=d",
                   "-drive", f"if=none,id=cdrom,media=cdrom,readonly=on,file={iso}",
                   "-device", "virtio-scsi-device", "-device", "scsi-cd,drive=cdrom"]
    else:
        raise ValueError(f"no QEMU profile for architecture {arch!r}")
    if mode == "uefi":
        if firmware is None:
            raise FileNotFoundError(f"no matching UEFI CODE/VARS firmware files for {arch}")
        code, _ = firmware
        common += ["-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
                   "-drive", f"if=pflash,format=raw,unit=1,file={vars_copy}"]
    return common


def run_boot_test(iso: Path, profile_name: str, profile: dict[str, Any], mode: str,
                  reporter: Reporter, timeout: float, firmware_args: tuple[str | None, str | None]) -> None:
    test_name = f"qemu_boot_{profile['architecture']}_{mode}"
    started = time.monotonic()
    if mode not in profile["boot_modes"]:
        reporter.add(test_name, "FAIL", f"mode {mode!r} is not supported by profile {profile_name}", started)
        return
    qemu = shutil.which(profile["qemu"])
    if qemu is None:
        reporter.add(test_name, "BLOCKED", f"required emulator {profile['qemu']} is not installed", started)
        return
    firmware: tuple[Path, Path] | None = None
    if mode == "uefi":
        try:
            firmware = discover_firmware(profile["uefi_firmware_family"], *firmware_args)
        except ValueError as e:
            reporter.add(test_name, "FAIL", str(e), started)
            return
        if firmware is None:
            reporter.add(test_name, "BLOCKED", f"matching UEFI firmware CODE/VARS unavailable for {profile['architecture']}", started)
            return

    vm_dir = reporter.evidence_dir / f"{profile_name}-{mode}"
    vm_dir.mkdir(parents=True, exist_ok=True)
    serial_log = vm_dir / "serial.log"
    qemu_log = vm_dir / "qemu.log"
    var_tmp: tempfile.TemporaryDirectory[str] | None = None
    proc: subprocess.Popen[bytes] | None = None
    qemu_stream = None
    try:
        var_copy = vm_dir / "vars.fd"
        if firmware:
            shutil.copyfile(firmware[1], var_copy)
        argv = _qemu_args(profile, mode, iso, serial_log, firmware, var_copy)
        qemu_stream = qemu_log.open("wb")
        qemu_stream.write(("QEMU argv: " + json.dumps(argv, ensure_ascii=False) + "\\n").encode())
        qemu_stream.flush()
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=qemu_stream,
                                stderr=subprocess.STDOUT, env=command_env(), start_new_session=True)
        deadline = time.monotonic() + timeout
        patterns = [re.compile(x, re.I) for x in profile["success_patterns"]]
        detected: str | None = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                qemu_stream.flush()
                serial_text = serial_log.read_text(encoding="utf-8", errors="replace") if serial_log.exists() else ""
                if any(p.search(serial_text) for p in patterns):
                    detected = "boot marker recorded before QEMU exited"
                    break
                reporter.add(test_name, "FAIL", f"QEMU exited before a boot-success marker (exit {proc.returncode})", started, qemu_log)
                return
            serial_text = serial_log.read_text(encoding="utf-8", errors="replace") if serial_log.exists() else ""
            for pattern in patterns:
                match = pattern.search(serial_text)
                if match:
                    detected = match.group(0)
                    break
            if detected:
                _kill_process_group(proc)
                qemu_stream.flush()
                break
            time.sleep(0.2)
        if not detected:
            _kill_process_group(proc)
            if qemu_stream:
                qemu_stream.flush()
            reporter.add(test_name, "FAIL", f"timeout after {timeout:g}s without a configured systemd boot marker", started, serial_log if serial_log.exists() else qemu_log)
            return
        reporter.add(test_name, "PASS", f"serial boot criterion observed: {detected}", started, serial_log)
    except OSError as e:
        if proc is not None:
            _kill_process_group(proc)
        reporter.add(test_name, "FAIL", f"QEMU failed to start or log files could not be accessed: {e}", started, qemu_log)
    except BaseException:
        if proc is not None:
            _kill_process_group(proc)
        raise
    finally:
        if proc is not None and proc.poll() is None:
            _kill_process_group(proc)
        if qemu_stream is not None:
            qemu_stream.close()
        try:
            var_copy.unlink(missing_ok=True)
        except UnboundLocalError:
            pass
        if var_tmp is not None:
            var_tmp.cleanup()


def download_iso(url: str, destination: Path, expected_sha256: str, timeout: float) -> str:
    if not SHA256_RE.fullmatch(expected_sha256):
        raise ValueError("remote ISO requires an expected SHA-256 of exactly 64 hex characters")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("ISO URL must be HTTPS and must not contain embedded credentials")
    request = urllib.request.Request(url, headers={"User-Agent": "ISO-Tests/0.1"})
    h = hashlib.sha256()
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme != "https":
                raise ValueError("download redirected to a non-HTTPS URL")
            header_size = response.headers.get("Content-Length")
            if header_size and int(header_size) > MAX_ISO_BYTES:
                raise ValueError(f"remote artifact exceeds size limit of {MAX_ISO_BYTES} bytes")
            with destination.open("xb") as out:
                created = True
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_ISO_BYTES:
                        raise ValueError(f"remote artifact exceeds size limit of {MAX_ISO_BYTES} bytes")
                    h.update(block)
                    out.write(block)
        if total == 0:
            raise ValueError("download returned an empty file")
        digest = h.hexdigest()
        if digest.lower() != expected_sha256.lower():
            raise ValueError("downloaded ISO SHA-256 does not match the supplied expected value")
        return digest
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def _install_signal_handlers() -> dict[int, Any]:
    previous: dict[int, Any] = {}
    def stop(signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt(f"received signal {signum}")
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.signal(sig, stop)
    return previous


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and smoke-test ChurrOS ISO images")
    parser.add_argument("iso", nargs="?", help="path to an authentic ISO; mutually exclusive with --iso-url")
    parser.add_argument("--iso-url", help="download an ISO via HTTPS (requires --expected-sha256)")
    parser.add_argument("--expected-sha256", help="trusted expected SHA-256; required for --iso-url")
    parser.add_argument("--sha256sums", type=Path, help="published SHA256SUMS file containing the exact ISO basename")
    parser.add_argument("--profile", required=True, help="profile key from profiles/churros.json")
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES, help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("all", "bios", "uefi"), default="all")
    parser.add_argument("--no-boot", action="store_true", help="run static checks only; boot checks are explicitly SKIP")
    parser.add_argument("--report-dir", type=Path, default=Path("artifacts"), help="JSON report and diagnostic output directory")
    parser.add_argument("--timeout", type=float, default=600, help="per QEMU boot timeout in seconds (max 1800)")
    parser.add_argument("--xorriso-timeout", type=float, default=120, help="per xorriso command timeout in seconds")
    parser.add_argument("--download-timeout", type=float, default=300, help="network read timeout in seconds")
    parser.add_argument("--uefi-code", help="explicit UEFI firmware CODE file")
    parser.add_argument("--uefi-vars", help="explicit UEFI firmware VARS template; copied before boot")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    reporter = Reporter(args.report_dir, safe_run_id())
    reporter.started_at = datetime.now(timezone.utc).isoformat()
    artifact: dict[str, Any] = {"name": Path(args.iso).name if args.iso else None,
                                "path": args.iso, "size_bytes": None, "sha256": None}
    profile_data: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None
    architecture: str | None = None
    tool_versions: dict[str, str] = {"python": sys.version.split()[0]}
    selected_modes: list[str] = []
    source_url = args.iso_url
    downloaded: Path | None = None
    download_temp_dir: Path | None = None
    exit_code = 1
    previous_handlers = _install_signal_handlers()
    try:
        if args.timeout <= 0 or args.timeout > 1800 or args.xorriso_timeout <= 0 or args.download_timeout <= 0:
            raise ValueError("timeouts must be positive; QEMU timeout cannot exceed 1800 seconds")
        if bool(args.iso) == bool(args.iso_url):
            raise ValueError("provide exactly one ISO path or --iso-url")
        if args.iso_url and not args.expected_sha256:
            raise ValueError("--iso-url requires --expected-sha256")
        if args.iso_url and args.sha256sums:
            raise ValueError("use --expected-sha256 for --iso-url; --sha256sums applies to local ISO basenames")
        if args.expected_sha256 and args.sha256sums:
            raise ValueError("choose only one checksum reference: --expected-sha256 or --sha256sums")
        if args.sha256sums and not args.sha256sums.is_file():
            raise ValueError(f"checksum manifest does not exist: {args.sha256sums}")
        profile_data = load_profiles(args.profiles)
        profile = profile_data["profiles"].get(args.profile)
        if profile is None:
            raise ValueError(f"unknown profile {args.profile!r}; available: {', '.join(profile_data['profiles'])}")
        architecture = profile["architecture"]
        modes = list(profile["boot_modes"])
        if args.mode != "all":
            if args.mode not in modes:
                raise ValueError(f"mode {args.mode!r} is not supported by profile {args.profile!r}")
            modes = [args.mode]
        selected_modes = modes
        iso = Path(args.iso).expanduser() if args.iso else None
        if args.iso_url:
            download_temp_dir = Path(tempfile.mkdtemp(prefix=".iso-download-", dir=reporter.report_dir))
            downloaded = download_temp_dir / "image.iso"
            started = time.monotonic()
            try:
                digest = download_iso(args.iso_url, downloaded, args.expected_sha256, args.download_timeout)
                reporter.add("artifact_download", "PASS", f"Downloaded {downloaded.stat().st_size} bytes; SHA-256 verified", started,
                             reporter.evidence_dir / "download.log")
                (reporter.evidence_dir / "download.log").write_text("HTTPS download completed; expected SHA-256 matched.\n", encoding="utf-8")
                iso = downloaded
            except Exception as e:
                if isinstance(e, urllib.error.HTTPError):
                    message = f"HTTPS server returned HTTP {e.code}"
                elif isinstance(e, urllib.error.URLError):
                    message = f"HTTPS download failed ({type(e.reason).__name__})"
                else:
                    message = str(e).replace(args.iso_url, safe_source_url(args.iso_url) or "[URL omitted]")
                reporter.add("artifact_download", "FAIL", message, started, reporter.evidence_dir / "download.log")
                (reporter.evidence_dir / "download.log").write_text(f"Download failed: {message}\n", encoding="utf-8")
                iso = downloaded
        assert iso is not None
        artifact.update({"name": iso.name, "path": str(iso), "size_bytes": iso.stat().st_size if iso.exists() else None})
        digest, tool_versions = validate_iso(iso, args.profile, profile, reporter,
                                             args.expected_sha256,
                                             args.sha256sums, args.xorriso_timeout)
        artifact["sha256"] = digest
        tool_versions[profile["qemu"]] = _tool_version(profile["qemu"])
        if args.no_boot:
            for mode in modes:
                reporter.add(f"qemu_boot_{architecture}_{mode}", "SKIP", "Static-only run requested; no boot claim is made", time.monotonic())
        else:
            for mode in modes:
                run_boot_test(iso, args.profile, profile, mode, reporter, args.timeout,
                              (args.uefi_code, args.uefi_vars))
        statuses = [t.status for t in reporter.tests]
        exit_code = 1 if any(s in {"FAIL", "BLOCKED"} for s in statuses) else 0
    except KeyboardInterrupt as e:
        reporter.add("execution", "BLOCKED", f"Interrupted; temporary processes were stopped ({e})", time.monotonic())
        exit_code = 130
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
        reporter.add("configuration", "FAIL", str(e), time.monotonic())
        exit_code = 2
    finally:
        if download_temp_dir is not None:
            shutil.rmtree(download_temp_dir, ignore_errors=True)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        path = reporter.write(artifact=artifact, profile=args.profile, architecture=architecture,
                              modes=selected_modes, tool_versions=tool_versions, source_url=safe_source_url(source_url))
    print(f"report: {path}")
    print(json.dumps({s: sum(t.status == s for t in reporter.tests)
                      for s in ("PASS", "FAIL", "SKIP", "BLOCKED")}, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
