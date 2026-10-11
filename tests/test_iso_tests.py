from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import iso_tests as it  # noqa: E402


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="iso tests with spaces ")
        self.root = Path(self.tmp.name)
        self.reporter = it.Reporter(self.root / "report", "unit-test")
        self.profile = it.load_profiles()["profiles"]["x86_64-niri"]
        self.iso = self.root / "unexpected name with spaces.iso"
        # Harness-only synthetic descriptor fixture, not a bootable ISO.
        pvd = bytearray(2048)
        pvd[0], pvd[1:6], pvd[6] = 1, b"CD001", 1
        pvd[80:84], pvd[84:88] = (32).to_bytes(4, "little"), (32).to_bytes(4, "big")
        pvd[128:130], pvd[130:132] = (2048).to_bytes(2, "little"), (2048).to_bytes(2, "big")
        with self.iso.open("wb") as f:
            f.truncate(32 * 2048)
            f.seek(16 * 2048)
            f.write(pvd)

    def tearDown(self):
        self.tmp.cleanup()

    def set_minimum_size(self):
        self._min = mock.patch.object(it, "MIN_ISO_BYTES", 1)
        self._min.start()
        self.addCleanup(self._min.stop)

    def fake_xorriso(self, output_for):
        bindir = self.root / "fake bin"
        bindir.mkdir(exist_ok=True)
        exe = bindir / "xorriso"
        src = "#!/usr/bin/env python3\nimport sys\na=' '.join(sys.argv[1:])\n"
        src += "print(" + repr(output_for) + ")\n"
        exe.write_text(src, encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bindir) + os.pathsep + self._old_path
        self.addCleanup(lambda: os.environ.__setitem__("PATH", self._old_path))
        return exe

    def fake_qemu(self, body: str, name: str = "qemu-system-x86_64") -> Path:
        bindir = self.root / "fake qemu bin"
        bindir.mkdir(exist_ok=True)
        exe = bindir / name
        exe.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bindir) + os.pathsep + self._old_path
        self.addCleanup(lambda: os.environ.__setitem__("PATH", self._old_path))
        return exe

    def fake_installer_tools(self, omit: tuple[str, ...] = (), omit_binary: bool = False) -> tuple[Path, Path]:
        bindir = self.root / "fake installer tools"
        bindir.mkdir(exist_ok=True)
        xorriso = bindir / "xorriso"
        xorriso.write_text("#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\ni=sys.argv.index('-extract')\np=Path(sys.argv[i+2]); p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b'fake squashfs')\n",
                          encoding="utf-8")
        contents = {
            "usr/local/bin/calamares": "#!/bin/bash\nexec sudo -n /usr/bin/calamares\n",
            "usr/share/applications/calamares.desktop": "Exec=/usr/local/bin/calamares\n",
            "etc/calamares/settings.conf": "sequence:\nbranding: churros\n",
            "etc/calamares/branding/churros/branding.desc": "Name=ChurrOS\n",
            "etc/calamares/modules/unpackfs.conf": f'source: "/run/archiso/bootmnt/churros/{self.profile["architecture"]}/airootfs.sfs"\n',
        }
        unsquashfs = bindir / "unsquashfs"
        body = "#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n"
        body += "contents=" + repr(contents) + "\nomit=" + repr(tuple(omit)) + "\n"
        body += "if '-cat' in sys.argv:\n    raise SystemExit(1 if " + repr(omit_binary) + " else 0)\n"
        body += "i=sys.argv.index('-d'); dest=Path(sys.argv[i+1]); files=sys.argv[i+3:]\n"
        body += "for name in files:\n    if name in omit: continue\n    p=dest/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(contents[name])\n    if name=='usr/local/bin/calamares': p.chmod(0o755)\n"
        unsquashfs.write_text(body, encoding="utf-8")
        for exe in (xorriso, unsquashfs):
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return xorriso, unsquashfs

    def fake_xorriso_extractor(self, files: dict[str, str]) -> Path:
        bindir = self.root / "fake xorriso extract bin"
        bindir.mkdir(exist_ok=True)
        exe = bindir / "xorriso"
        body = "#!/usr/bin/env python3\nimport sys,json\nfrom pathlib import Path\n"
        body += "files=" + repr(files) + "\na=sys.argv; i=a.index('-extract'); name=a[i+1]; dest=Path(a[i+2])\n"
        body += "if name not in files: raise SystemExit(3)\ndest.parent.mkdir(parents=True,exist_ok=True); dest.write_text(files[name])\n"
        exe.write_text(body, encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return exe

    def test_serial_console_config_checks_mode_specific_kernel_args(self):
        files = {"/boot/syslinux/archiso_sys-linux.cfg": "APPEND archisobasedir=churros console=ttyS0,115200"}
        xorriso = self.fake_xorriso_extractor(files)
        with mock.patch.object(it.shutil, "which", return_value=str(xorriso)):
            ok = it.validate_serial_console_config(self.iso, "x86_64-niri", self.profile,
                                                   "bios", self.reporter, 5)
        self.assertTrue(ok)
        self.assertEqual(self.reporter.tests[-1].status, "PASS")

    def test_serial_console_config_fails_when_kernel_console_is_missing(self):
        files = {"/boot/syslinux/archiso_sys-linux.cfg": "APPEND archisobasedir=churros quiet"}
        xorriso = self.fake_xorriso_extractor(files)
        with mock.patch.object(it.shutil, "which", return_value=str(xorriso)):
            ok = it.validate_serial_console_config(self.iso, "x86_64-niri", self.profile,
                                                   "bios", self.reporter, 5)
        self.assertFalse(ok)
        self.assertEqual(self.reporter.tests[-1].status, "FAIL")
        self.assertIn("console=ttyS0", self.reporter.tests[-1].message)

    def test_serial_console_config_missing_from_iso_is_a_failure(self):
        xorriso = self.fake_xorriso_extractor({})
        with mock.patch.object(it.shutil, "which", return_value=str(xorriso)):
            ok = it.validate_serial_console_config(self.iso, "x86_64-niri", self.profile,
                                                   "bios", self.reporter, 5)
        self.assertFalse(ok)
        self.assertEqual(self.reporter.tests[-1].status, "FAIL")
        self.assertIn("missing or unreadable", self.reporter.tests[-1].message)

    def test_installer_payload_check_confirms_static_files_and_markers_only(self):
        xorriso, unsquashfs = self.fake_installer_tools()
        paths = {"xorriso": xorriso, "unsquashfs": unsquashfs}
        with mock.patch.object(it.shutil, "which", side_effect=lambda name: str(paths[name])):
            it.validate_installer_payload(self.iso, self.profile,
                                          it.load_profiles()["installer_payload"], self.reporter, 5)
        result = next(t for t in self.reporter.tests if t.name == "installer_live_payload")
        self.assertEqual(result.status, "PASS")
        self.assertIn("does not run the GUI", result.message)
        manifest = self.reporter.report_dir / result.log_path
        self.assertTrue(manifest.is_file())
        self.assertIn("etc/calamares/settings.conf", manifest.read_text(encoding="utf-8"))

    def test_installer_payload_missing_configuration_is_failure(self):
        xorriso, unsquashfs = self.fake_installer_tools(("etc/calamares/settings.conf",))
        paths = {"xorriso": xorriso, "unsquashfs": unsquashfs}
        with mock.patch.object(it.shutil, "which", side_effect=lambda name: str(paths[name])):
            it.validate_installer_payload(self.iso, self.profile,
                                          it.load_profiles()["installer_payload"], self.reporter, 5)
        result = next(t for t in self.reporter.tests if t.name == "installer_live_payload")
        self.assertEqual(result.status, "FAIL")
        self.assertIn("settings.conf", result.message)

    def test_installer_payload_missing_calamares_binary_is_failure(self):
        xorriso, unsquashfs = self.fake_installer_tools(omit_binary=True)
        paths = {"xorriso": xorriso, "unsquashfs": unsquashfs}
        with mock.patch.object(it.shutil, "which", side_effect=lambda name: str(paths[name])):
            it.validate_installer_payload(self.iso, self.profile,
                                          it.load_profiles()["installer_payload"], self.reporter, 5)
        result = next(t for t in self.reporter.tests if t.name == "installer_live_payload")
        self.assertEqual(result.status, "FAIL")
        self.assertIn("usr/bin/calamares missing", result.message)

    def test_installer_payload_check_is_blocked_without_unsquashfs(self):
        xorriso, _ = self.fake_installer_tools()
        with mock.patch.object(it.shutil, "which", side_effect=lambda name: str(xorriso) if name == "xorriso" else None):
            it.validate_installer_payload(self.iso, self.profile,
                                          it.load_profiles()["installer_payload"], self.reporter, 5)
        result = next(t for t in self.reporter.tests if t.name == "installer_live_payload")
        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("unsquashfs", result.message)

    def test_profile_table_matches_real_architectures_and_modes(self):
        p = it.load_profiles()["profiles"]
        self.assertEqual({n for n in p if n.startswith("x86_64-")}, {
            "x86_64-niri", "x86_64-xfce", "x86_64-kde", "x86_64-server"})
        self.assertEqual(p["aarch64-niri"]["boot_modes"], ["uefi"])
        self.assertEqual(p["aarch64-niri"]["efi_bootloader"], "BOOTAA64.EFI")

    def test_rejects_empty_profile_table_and_invalid_architecture(self):
        for data, text in [
            ({"schema_version": 1, "profiles": {}}, "non-empty"),
            ({"schema_version": 1, "profiles": {"arm": {"architecture": "armv7"}}}, "missing required"),
        ]:
            path = self.root / "bad.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, text):
                it.load_profiles(path)

    def test_rejects_bios_profile_on_aarch64(self):
        path = self.root / "bad-arm.json"
        path.write_text(json.dumps({"schema_version": 1, "profiles": {
            "arm": {"architecture": "aarch64", "edition": "niri", "boot_modes": ["bios"],
                    "qemu": "qemu-system-aarch64", "serial_device": "ttyAMA0",
                    "success_patterns": ["Reached target"]}}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "BIOS is not supported"):
            it.load_profiles(path)

    def test_nonexistent_artifact_is_failure_not_pass(self):
        digest, _ = it.validate_iso(self.root / "absent.iso", "x86_64-niri", self.profile,
                                    self.reporter, None, None, 1)
        self.assertIsNone(digest)
        self.assertEqual(self.reporter.tests[0].status, "FAIL")
        self.assertTrue(all(t.status == "SKIP" for t in self.reporter.tests[1:]))

    def test_empty_artifact_fails_and_is_never_an_iso(self):
        self.set_minimum_size()
        self.iso.write_bytes(b"")
        with mock.patch.object(it.shutil, "which", return_value=None):
            it.validate_iso(self.iso, "x86_64-niri", self.profile, self.reporter, None, None, 1)
        by_name = {t.name: t.status for t in self.reporter.tests}
        self.assertEqual(by_name["artifact_size"], "FAIL")
        self.assertEqual(by_name["iso9660_primary_volume_descriptor"], "FAIL")
        self.assertEqual(by_name["xorriso_el_torito"], "BLOCKED")

    def test_corrupt_or_truncated_file_fails_descriptor_test(self):
        self.set_minimum_size()
        self.iso.write_bytes(b"not a CD image")
        with mock.patch.object(it.shutil, "which", return_value=None):
            it.validate_iso(self.iso, "x86_64-niri", self.profile, self.reporter, None, None, 1)
        self.assertEqual(next(t.status for t in self.reporter.tests if t.name == "iso9660_primary_volume_descriptor"), "FAIL")

    def test_declared_iso_volume_larger_than_artifact_is_reported_as_truncated(self):
        self.set_minimum_size()
        with self.iso.open("r+b") as f:
            f.seek(16 * 2048 + 80)
            f.write((100).to_bytes(4, "little") + (100).to_bytes(4, "big"))
        with mock.patch.object(it.shutil, "which", return_value=None):
            it.validate_iso(self.iso, "x86_64-niri", self.profile, self.reporter, None, None, 1)
        result = next(t for t in self.reporter.tests if t.name == "iso9660_primary_volume_descriptor")
        self.assertEqual(result.status, "FAIL")
        self.assertIn("artifact is truncated", result.message)

    def test_missing_xorriso_is_blocked_not_skipped_or_pass(self):
        self.set_minimum_size()
        with mock.patch.object(it.shutil, "which", return_value=None):
            it.validate_iso(self.iso, "x86_64-niri", self.profile, self.reporter, None, None, 1)
        self.assertEqual(next(t.status for t in self.reporter.tests if t.name == "xorriso_el_torito"), "BLOCKED")
        self.assertEqual(next(t.status for t in self.reporter.tests if t.name == "boot_structure_bios"), "BLOCKED")

    def test_actual_hash_is_reported_but_not_misrepresented_as_published_checksum(self):
        self.set_minimum_size()
        with mock.patch.object(it.shutil, "which", return_value=None):
            digest, _ = it.validate_iso(self.iso, "x86_64-niri", self.profile, self.reporter, None, None, 1)
        self.assertEqual(digest, hashlib.sha256(self.iso.read_bytes()).hexdigest())
        self.assertEqual(next(t.status for t in self.reporter.tests if t.name == "published_checksum"), "SKIP")

    def test_checksum_mismatch_is_failure(self):
        self.set_minimum_size()
        expected = "0" * 64
        with mock.patch.object(it.shutil, "which", return_value=None):
            it.validate_iso(self.iso, "x86_64-niri", self.profile, self.reporter, expected, None, 1)
        self.assertEqual(next(t.status for t in self.reporter.tests if t.name == "published_checksum"), "FAIL")

    def test_sha256sums_requires_exact_unambiguous_basename(self):
        sums = self.root / "SHA256SUMS"
        sums.write_text("a" * 64 + "  wrong-file.iso\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "found 0"):
            it.parse_sha256sums(sums, self.iso)
        sums.write_text("a" * 64 + "  " + self.iso.name + "\n" + "b" * 64 + "  " + self.iso.name + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "found 2"):
            it.parse_sha256sums(sums, self.iso)

    def test_static_validator_detects_boot_catalog_and_embedded_uefi_image(self):
        self.set_minimum_size()
        self.fake_xorriso("El Torito catalog : 23 1\\nEl Torito boot img : 24 4 BIOS y none\\nEl Torito boot img : 25 1 UEFI y none")
        it.validate_iso(self.iso, "x86_64-niri", self.profile, self.reporter, None, None, 2)
        by_name = {t.name: t.status for t in self.reporter.tests}
        self.assertEqual(by_name["xorriso_el_torito"], "PASS")
        self.assertEqual(by_name["boot_structure_bios"], "PASS")
        self.assertEqual(by_name["boot_structure_uefi"], "PASS")
        self.assertEqual(by_name["efi_bootloader_file"], "SKIP")
        self.assertIn("embedded El Torito image", next(t.message for t in self.reporter.tests if t.name == "efi_bootloader_file"))

    def test_static_validator_rejects_missing_expected_uefi_entry_and_loader(self):
        self.set_minimum_size()
        self.fake_xorriso("El Torito catalog : 23 1\\nEl Torito boot img : 24 4 BIOS y none\\nno efi path")
        it.validate_iso(self.iso, "x86_64-niri", self.profile, self.reporter, None, None, 2)
        by_name = {t.name: t.status for t in self.reporter.tests}
        self.assertEqual(by_name["boot_structure_uefi"], "FAIL")
        self.assertEqual(by_name["efi_bootloader_file"], "FAIL")

    def test_xorriso_timeout_is_failure_and_log_is_kept(self):
        self.set_minimum_size()
        self.fake_xorriso("import time; time.sleep(10)")
        it.validate_iso(self.iso, "x86_64-niri", self.profile, self.reporter, None, None, 0.1)
        result = next(t for t in self.reporter.tests if t.name == "xorriso_el_torito")
        self.assertEqual(result.status, "FAIL")
        self.assertTrue((self.reporter.report_dir / result.log_path).exists())

    def _qemu_profile(self, modes=("bios",)):
        profile = dict(self.profile)
        profile["boot_modes"] = list(modes)
        return profile

    def test_missing_qemu_is_blocked(self):
        with mock.patch.object(it.shutil, "which", return_value=None):
            it.run_boot_test(self.iso, "x86_64-niri", self._qemu_profile(), "bios", self.reporter, 1, (None, None))
        self.assertEqual(self.reporter.tests[-1].status, "BLOCKED")

    def test_qemu_timeout_without_marker_is_failure(self):
        self.fake_qemu("import time\ntime.sleep(10)\n")
        it.run_boot_test(self.iso, "x86_64-niri", self._qemu_profile(), "bios", self.reporter, 0.2, (None, None))
        self.assertEqual(self.reporter.tests[-1].status, "FAIL")
        self.assertIn("without a configured", self.reporter.tests[-1].message)

    def test_qemu_error_exit_without_marker_is_failure(self):
        self.fake_qemu("import sys\nprint('emulator error')\nsys.exit(23)\n")
        it.run_boot_test(self.iso, "x86_64-niri", self._qemu_profile(), "bios", self.reporter, 2, (None, None))
        self.assertEqual(self.reporter.tests[-1].status, "FAIL")
        self.assertIn("exit 23", self.reporter.tests[-1].message)

    def test_qemu_requires_profile_marker_and_records_serial_evidence(self):
        self.fake_qemu("import sys,time\na=sys.argv\np=a[a.index('-serial')+1].split(':',1)[1]\nopen(p,'w').write('systemd[1]: Reached target Graphical Interface.\\n')\ntime.sleep(10)\n")
        it.run_boot_test(self.iso, "x86_64-niri", self._qemu_profile(), "bios", self.reporter, 2, (None, None))
        self.assertEqual(self.reporter.tests[-1].status, "PASS")
        self.assertTrue((self.reporter.report_dir / self.reporter.tests[-1].log_path).exists())

    def test_unsupported_profile_mode_fails(self):
        p = dict(self.profile)
        p["architecture"] = "aarch64"
        p["boot_modes"] = ["uefi"]
        it.run_boot_test(self.iso, "bad", p, "bios", self.reporter, 1, (None, None))
        self.assertEqual(self.reporter.tests[-1].status, "FAIL")

    def test_ubuntu_24_04_ovmf_4m_files_are_discovered(self):
        code = Path("/usr/share/OVMF/OVMF_CODE_4M.fd")
        variables = Path("/usr/share/OVMF/OVMF_VARS_4M.fd")
        allowed = {str(code), str(variables)}
        real_stat = Path.stat
        def fake_stat(path, *args, **kwargs):
            if str(path) == str(code):
                return mock.Mock(st_size=4 * 1024 * 1024)
            if str(path) == str(variables):
                return mock.Mock(st_size=540672)
            return real_stat(path, *args, **kwargs)
        with mock.patch.object(Path, "is_file", autospec=True,
                               side_effect=lambda path: str(path) in allowed), mock.patch.object(Path, "stat", autospec=True, side_effect=fake_stat):
            self.assertEqual(it.discover_firmware("x86_64", None, None), (code, variables))

    def test_uefi_firmware_pair_allows_different_sizes_but_rejects_empty_files(self):
        code = self.root / "code.fd"
        vars_file = self.root / "vars.fd"
        code.write_bytes(b"C" * 4096)
        vars_file.write_bytes(b"V" * 540672)
        self.assertEqual(it.discover_firmware("x86_64", str(code), str(vars_file)), (code, vars_file))
        vars_file.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            it.discover_firmware("x86_64", str(code), str(vars_file))

    def test_qemu_profiles_use_the_expected_machine_and_disable_network(self):
        x86 = self._qemu_profile(("bios", "uefi"))
        x86_args = it._qemu_args(x86, "bios", self.iso, self.root / "serial log.txt", None, self.root / "vars.fd")
        self.assertIn("q35,accel=tcg", x86_args)
        self.assertIn("-nic", x86_args)
        self.assertEqual(x86_args[x86_args.index("-nic") + 1], "none")
        self.assertIn(str(self.iso), x86_args)
        self.assertFalse(any("qcow2" in arg or "host_device" in arg for arg in x86_args))
        arm = it.load_profiles()["profiles"]["aarch64-niri"]
        code, vars_template = self.root / "AAVMF_CODE.fd", self.root / "AAVMF_VARS.fd"
        code.write_bytes(b"C" * 4096)
        vars_template.write_bytes(b"V" * 4096)
        arm_args = it._qemu_args(arm, "uefi", self.iso, self.root / "serial.log",
                                 (code, vars_template), self.root / "temporary-vars.fd")
        self.assertIn("virt,accel=tcg", arm_args)
        self.assertIn("cortex-a72", arm_args)
        self.assertEqual(arm_args[arm_args.index("-nic") + 1], "none")
        self.assertTrue(any("readonly=on" in arg and str(self.iso) in arg for arg in arm_args))
        self.assertEqual(vars_template.read_bytes(), b"V" * 4096)

    def test_report_url_redaction_drops_query_and_fragment(self):
        url = "https://downloads.example/churros.iso?token=private-value&sig=secret#fragment"
        safe = it.safe_source_url(url)
        self.assertEqual(safe, "https://downloads.example/churros.iso")
        self.assertNotIn("private-value", safe)
        self.assertNotIn("secret", safe)

    def test_remote_download_requires_https_and_checksum_and_cleans_mismatch(self):
        dst = self.root / "download.iso"
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            it.download_iso("http://example.invalid/image.iso", dst, "a" * 64, 1)
        self.assertFalse(dst.exists())
        with mock.patch.object(it.urllib.request, "urlopen", side_effect=OSError("network unavailable")):
            with self.assertRaisesRegex(OSError, "network unavailable"):
                it.download_iso("https://example.invalid/image.iso", dst, "a" * 64, 1)
        self.assertFalse(dst.exists())

    def test_unverified_https_download_computes_but_does_not_assert_a_trusted_hash(self):
        payload = b"fixture bytes, not an ISO"
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://example.invalid/image.iso"
        response.headers = {"Content-Length": str(len(payload))}
        response.read.side_effect = [payload, b""]
        dst = self.root / "unverified download.iso"
        with mock.patch.object(it.urllib.request, "urlopen", return_value=response):
            digest = it.download_iso("https://example.invalid/image.iso", dst, None, 1)
        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
        self.assertEqual(dst.read_bytes(), payload)

    def test_unverified_download_rejects_cross_host_redirect_and_cleans_partial_file(self):
        payload = b"redirected content"
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://other.example/image.iso"
        response.headers = {"Content-Length": str(len(payload))}
        response.read.side_effect = [payload, b""]
        dst = self.root / "redirected.iso"
        with mock.patch.object(it.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "different HTTPS host"):
                it.download_iso("https://official.example/image.iso", dst, None, 1)
        self.assertFalse(dst.exists())

    def test_download_url_requires_hash_or_explicit_unverified_opt_in(self):
        report_dir = self.root / "no hash report"
        rc = it.main(["--iso-url", "https://example.invalid/image.iso", "--profile", "x86_64-niri",
                      "--report-dir", str(report_dir)])
        self.assertEqual(rc, 2)
        report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
        self.assertIn("exactly one", report["tests"][0]["message"])

    def test_unverified_cli_marks_checksum_skip_and_cleans_download(self):
        payload = b"untrusted synthetic bytes"
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://example.invalid/image.iso"
        response.headers = {"Content-Length": str(len(payload))}
        response.read.side_effect = [payload, b""]
        report_dir = self.root / "unverified report"
        with mock.patch.object(it.urllib.request, "urlopen", return_value=response), mock.patch.object(it.shutil, "which", return_value=None):
            rc = it.main(["--iso-url", "https://example.invalid/image.iso", "--allow-unverified-download",
                          "--profile", "x86_64-niri", "--no-boot", "--report-dir", str(report_dir)])
        self.assertEqual(rc, 1)  # fixture is intentionally not a plausible ISO
        report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
        by_name = {t["name"]: t["status"] for t in report["tests"]}
        self.assertEqual(by_name["artifact_download"], "PASS")
        self.assertEqual(by_name["published_checksum"], "SKIP")
        self.assertEqual(list(report_dir.glob(".iso-download-*")), [])

    def test_download_content_length_mismatch_rejects_truncation_and_cleans_partial_file(self):
        payload = b"only part of response"
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://example.invalid/image.iso"
        response.headers = {"Content-Length": str(len(payload) + 25)}
        response.read.side_effect = [payload, b""]
        dst = self.root / "truncated.iso"
        with mock.patch.object(it.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "download size mismatch"):
                it.download_iso("https://example.invalid/image.iso", dst, None, 1)
        self.assertFalse(dst.exists())

    def test_download_checksum_mismatch_cleans_its_partial_file_only(self):
        payload = b"downloaded content"
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://example.invalid/image.iso"
        response.headers = {"Content-Length": str(len(payload))}
        response.read.side_effect = [payload, b""]
        dst = self.root / "download.iso"
        with mock.patch.object(it.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "does not match"):
                it.download_iso("https://example.invalid/image.iso?token=secret", dst, "0" * 64, 1)
        self.assertFalse(dst.exists())

    def test_download_never_overwrites_or_deletes_preexisting_file(self):
        dst = self.root / "download.iso"
        dst.write_bytes(b"keep me")
        payload = b"new payload"
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://example.invalid/image.iso"
        response.headers = {"Content-Length": str(len(payload))}
        response.read.side_effect = [payload, b""]
        with mock.patch.object(it.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(FileExistsError):
                it.download_iso("https://example.invalid/image.iso", dst, hashlib.sha256(payload).hexdigest(), 1)
        self.assertEqual(dst.read_bytes(), b"keep me")

    def test_report_has_explicit_status_counts_and_tool_versions(self):
        self.reporter.add("fixture_test", "SKIP", "not run", __import__("time").monotonic())
        path = self.reporter.write(artifact={"name": self.iso.name}, profile="unit-fixture",
                                  architecture="x86_64", modes=["bios"], tool_versions={"python": "test"})
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["SKIP"], 1)
        self.assertEqual(report["tests"][0]["status"], "SKIP")
        self.assertIn("run_id", report)

    def test_run_command_does_not_use_shell_and_timeout_terminates_child(self):
        exe = self.root / "slow tool.py"
        exe.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n", encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        log = self.root / "command.log"
        rc, _out, err = it.run_command([str(exe), "name with spaces; not shell"], 0.1, log)
        self.assertIsNone(rc)
        self.assertIn("timed out", err)
        self.assertTrue(log.exists())

    def test_interruption_leaves_no_downloaded_artifact_and_writes_report(self):
        report_dir = self.root / "interrupted report"
        def interrupted(_url, destination, _sha, _timeout):
            destination.write_bytes(b"partial")
            raise KeyboardInterrupt("test interrupt")
        with mock.patch.object(it, "download_iso", side_effect=interrupted):
            rc = it.main(["--iso-url", "https://example.invalid/image.iso", "--expected-sha256", "a" * 64,
                          "--profile", "x86_64-niri", "--no-boot", "--report-dir", str(report_dir)])
        self.assertEqual(rc, 130)
        self.assertEqual(list(report_dir.glob(".iso-download-*")), [])
        self.assertTrue((report_dir / "report.json").exists())
        report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
        self.assertIn("execution", [t["name"] for t in report["tests"]])

    def test_cli_unknown_profile_fails_and_preserves_structured_diagnostics(self):
        report_dir = self.root / "bad profile output"
        rc = it.main([str(self.iso), "--profile", "unknown", "--report-dir", str(report_dir)])
        self.assertEqual(rc, 2)
        report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["tests"][0]["status"], "FAIL")
        self.assertIn("unknown profile", report["tests"][0]["message"])


if __name__ == "__main__":
    unittest.main()
