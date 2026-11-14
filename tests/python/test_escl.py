"""Driverless scanning over eSCL (SCANNER_DRIVER=escl, sane-airscan).

script/escl.py translates the Brother option names, picks the paper source
from the device status and waits for the device to be idle; scanner.scan_cmd
applies it only when the entrypoint selected the escl driver.
"""
import io

import pytest

import escl
import scanner

STATUS_LOADED = (
    '<?xml version="1.0" encoding="UTF-8"?><scan:ScannerStatus '
    'xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm" '
    'xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">'
    "<pwg:Version>2.63</pwg:Version><pwg:State>Idle</pwg:State>"
    "<scan:AdfState>ScannerAdfLoaded</scan:AdfState></scan:ScannerStatus>"
)


# --- option names ----------------------------------------------------------

@pytest.mark.parametrize("brother, airscan", [
    ("True Gray", "Gray"),
    ("24bit Color", "Color"),
    ("24bit Color[Fast]", "Color"),
    ("Black & White", "Lineart"),
    ("Gray[Error Diffusion]", "Gray"),
    ("Color", "Color"),
    ("Something else", "Something else"),
])
def test_map_mode(brother, airscan):
    assert escl.map_mode(brother) == airscan


@pytest.mark.parametrize("brother, airscan", [
    ("FlatBed", "Flatbed"),
    ("Flatbed", "Flatbed"),
    ("Automatic Document Feeder(left aligned)", "ADF"),
    ("Automatic Document Feeder(left aligned,Duplex)", "ADF Duplex"),
    ("ADF", "ADF"),
    ("ADF Duplex", "ADF Duplex"),
])
def test_map_source(brother, airscan):
    assert escl.map_source(brother) == airscan


def test_translate_args_leaves_other_arguments_alone():
    args = ["--mode=True Gray", "--source=FlatBed", "--resolution=300", "-x", "215"]
    assert escl.translate_args(args) == ["--mode=Gray", "--source=Flatbed", "--resolution=300", "-x", "215"]


# --- device status ---------------------------------------------------------

def test_parse_status_reads_state_and_adf():
    assert escl.parse_status(STATUS_LOADED) == {"state": "Idle", "adf": "ScannerAdfLoaded"}
    assert escl.parse_status("not xml") == {"state": None, "adf": None}


def test_default_source_follows_the_feeder():
    assert escl.default_source({"state": "Idle", "adf": "ScannerAdfLoaded"}) == "ADF"
    assert escl.default_source({"state": "Idle", "adf": "ScannerAdfEmpty"}) == "Flatbed"
    assert escl.default_source(None) == "Flatbed"


def test_escl_url(clean_env):
    clean_env.delenv("ESCL_URL", raising=False)
    clean_env.setenv("IPADDRESS", "192.168.1.5")
    assert escl.escl_url() == "http://192.168.1.5/eSCL"
    clean_env.setenv("ESCL_URL", "http://scanner.local/eSCL/")
    assert escl.escl_url() == "http://scanner.local/eSCL"


def test_wait_until_idle_polls_until_idle(monkeypatch):
    answers = iter([{"state": "Processing", "adf": None}, {"state": "Idle", "adf": "ScannerAdfEmpty"}])
    monkeypatch.setattr(escl, "scanner_status", lambda: next(answers))
    monkeypatch.setattr(escl.time, "sleep", lambda _s: None)
    assert escl.wait_until_idle(timeout_s=10) == {"state": "Idle", "adf": "ScannerAdfEmpty"}


def test_wait_until_idle_gives_up_after_the_timeout(monkeypatch):
    monkeypatch.setattr(escl, "scanner_status", lambda: {"state": "Processing", "adf": None})
    monkeypatch.setattr(escl.time, "sleep", lambda _s: None)
    assert escl.wait_until_idle(timeout_s=0)["state"] == "Processing"


def test_active_driver(tmp_path):
    path = tmp_path / "driver"
    path.write_text("escl\n")
    assert escl.is_escl(str(path)) is True
    path.write_text("brscan4\n")
    assert escl.is_escl(str(path)) is False
    assert escl.is_escl(str(tmp_path / "missing")) is False


# --- scan_cmd --------------------------------------------------------------

@pytest.fixture
def captured(monkeypatch):
    monkeypatch.setattr(escl, "scanner_status", lambda: {"state": "Idle", "adf": "ScannerAdfLoaded"})
    monkeypatch.setattr(escl, "settle_after_button", lambda: None)
    commands = []
    monkeypatch.setattr(scanner, "execute_command", lambda log, command, **kw: commands.append(command))
    return commands


def test_scan_cmd_escl_translates_and_picks_the_feeder(clean_env, captured, monkeypatch):
    monkeypatch.setattr(escl, "is_escl", lambda: True)
    monkeypatch.setattr(escl, "wait_until_idle", lambda: {"state": "Idle", "adf": "ScannerAdfLoaded"})
    scanner.scan_cmd(io.StringIO(), None, "/tmp/x%04d.pnm", ["--mode=True Gray"])
    command = captured[0]
    assert "--mode=Gray" in command
    assert "--source=ADF" in command


def test_scan_cmd_escl_keeps_a_requested_source(clean_env, captured, monkeypatch):
    monkeypatch.setattr(escl, "is_escl", lambda: True)
    monkeypatch.setattr(escl, "wait_until_idle", lambda: {"state": "Idle", "adf": "ScannerAdfLoaded"})
    clean_env.setenv("SCAN_SOURCE", "FlatBed")
    scanner.scan_cmd(io.StringIO(), None, "/tmp/x%04d.pnm", [])
    sources = [arg for arg in captured[0] if arg.startswith("--source=")]
    assert sources == ["--source=Flatbed"]


def test_scan_cmd_brother_driver_is_unchanged(clean_env, captured, monkeypatch):
    monkeypatch.setattr(escl, "is_escl", lambda: False)
    scanner.scan_cmd(io.StringIO(), None, "/tmp/x%04d.pnm", ["--mode=True Gray"])
    command = captured[0]
    assert "--mode=True Gray" in command
    assert not any(arg.startswith("--source=") for arg in command)


# --- COPY_TO_DIR -----------------------------------------------------------

def test_copy_to_dir_copies_the_finished_pdf(clean_env, tmp_path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    target = tmp_path / "consume"
    clean_env.setenv("COPY_TO_DIR", str(target))
    scanner.copy_to_dir(io.StringIO(), str(source))
    assert (target / "scan.pdf").read_bytes() == b"%PDF-1.4 test"
    assert source.exists()
    assert [p.name for p in target.iterdir()] == ["scan.pdf"]


def test_copy_to_dir_off_by_default(clean_env, tmp_path):
    clean_env.delenv("COPY_TO_DIR", raising=False)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"x")
    scanner.copy_to_dir(io.StringIO(), str(source))
    assert [p.name for p in tmp_path.iterdir()] == ["scan.pdf"]


def test_scan_targets_parse_and_validate(clean_env):
    clean_env.setenv("SCAN_TARGETS", "PersonA=/share/a; PersonB = /share/b ;bad name=/x;PersonA=/dup;NoDir;Rel=relative")
    assert scanner.scan_targets() == {"PersonA": "/share/a", "PersonB": "/share/b", "NoDir": "", "Rel": ""}


def test_copy_to_dir_uses_the_target_folder(clean_env, tmp_path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"x")
    clean_env.setenv("COPY_TO_DIR", str(tmp_path / "default"))
    clean_env.setenv("SCAN_TARGETS", f"PersonA={tmp_path / 'a'};PersonB=")
    scanner.copy_to_dir(io.StringIO(), str(source), "PersonA")
    assert (tmp_path / "a" / "scan.pdf").exists()
    # a target without a folder, an unknown one and none use COPY_TO_DIR
    for target in ("PersonB", "Unknown", ""):
        scanner.copy_to_dir(io.StringIO(), str(source), target)
    assert (tmp_path / "default" / "scan.pdf").exists()


def test_the_picked_panel_name_becomes_the_job_target(clean_env):
    clean_env.setenv("SCAN_TARGETS", "PersonA=/share/a;PersonB=/share/b")
    clean_env.setenv("SKEY_USER", "PersonB")
    scanner.apply_scan_target()
    assert scanner.job_trigger(False)["target"] == "PersonB"
    assert "target" not in scanner.job_trigger(True)
    # a name that is not a target (stale entry, other host) is not recorded
    clean_env.setenv("SKEY_USER", "Stranger")
    scanner.apply_scan_target()
    assert "target" not in scanner.job_trigger(False)


def test_waiting_for_rear_pages_ends_early_on_the_finish_flag(clean_env, tmp_path, monkeypatch):
    monkeypatch.setattr(scanner.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(scanner, "set_status", lambda _s: None)
    monkeypatch.setattr(scanner, "clear_status", lambda _s: None)
    converted = []
    monkeypatch.setattr(scanner, "convert_and_post_process", lambda job, side, _t: converted.append((job, side)))
    job = "2026-01-02-03-04-05"
    (tmp_path / job).mkdir()
    (tmp_path / job / scanner.FINISH_FLAG).write_text("finish\n")
    clean_env.setenv("REAR_PAGE_WAIT_SECONDS", "600")
    started = scanner.time.monotonic()
    scanner.wait_for_rear_pages_or_convert(job)
    assert scanner.time.monotonic() - started < 5
    assert converted == [(job, "front")]


# --- refused jobs after a button press -------------------------------------

def _refused(command):
    import subprocess
    raise subprocess.CalledProcessError(7, command, output="scanimage: sane_start: Error during device I/O")


def test_escl_scan_is_retried_while_the_paper_has_not_moved(clean_env, monkeypatch, tmp_path):
    calls = []

    def fake(log, command, **kw):
        calls.append(command)
        (tmp_path / "x0001.pnm").write_bytes(b"partial")
        if len(calls) == 1:
            _refused(command)

    monkeypatch.setattr(scanner, "execute_command", fake)
    monkeypatch.setattr(escl, "scanner_status", lambda: {"state": "Idle", "adf": "ScannerAdfLoaded"})
    monkeypatch.setattr(scanner.time, "sleep", lambda _s: None)
    scanner.run_escl_scan(io.StringIO(), ["scanimage", "--source=ADF"], str(tmp_path / "x%04d.pnm"))
    assert len(calls) == 2


def test_escl_scan_is_not_retried_once_paper_moved(clean_env, monkeypatch, tmp_path):
    import subprocess
    calls = []
    monkeypatch.setattr(scanner, "execute_command", lambda log, command, **kw: (calls.append(command), _refused(command)))
    states = iter([{"adf": "ScannerAdfLoaded"}, {"adf": "ScannerAdfEmpty"}])
    monkeypatch.setattr(escl, "scanner_status", lambda: next(states))
    monkeypatch.setattr(scanner.time, "sleep", lambda _s: None)
    with pytest.raises(subprocess.CalledProcessError):
        scanner.run_escl_scan(io.StringIO(), ["scanimage", "--source=ADF"], str(tmp_path / "x%04d.pnm"))
    assert len(calls) == 1


def test_escl_scan_gives_up_after_the_retries(clean_env, monkeypatch, tmp_path):
    import subprocess
    calls = []
    monkeypatch.setattr(scanner, "execute_command", lambda log, command, **kw: (calls.append(command), _refused(command)))
    monkeypatch.setattr(scanner.time, "sleep", lambda _s: None)
    clean_env.setenv("ESCL_RETRIES", "1")
    with pytest.raises(subprocess.CalledProcessError):
        scanner.run_escl_scan(io.StringIO(), ["scanimage", "--source=Flatbed"], str(tmp_path / "x%04d.pnm"))
    assert len(calls) == 2


def test_button_scans_wait_for_the_device(clean_env, monkeypatch):
    slept = []
    monkeypatch.setattr(escl.time, "sleep", slept.append)
    clean_env.delenv("SKEY_FUNC", raising=False)
    escl.settle_after_button()
    assert slept == []
    clean_env.setenv("SKEY_FUNC", "FILE")
    escl.settle_after_button()
    assert slept == [5]
    clean_env.setenv("ESCL_BUTTON_DELAY", "0")
    escl.settle_after_button()
    assert slept == [5]
