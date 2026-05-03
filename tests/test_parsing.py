"""
Tests for golf shot log parsing.

Covers parse_shot, parse_club, parse_status, heartbeat detection,
the LogTailer._process dispatch logic, and null-byte stripping.
"""

import io
import os
import sys
import tempfile
import threading
import time
from queue import Queue

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import (
    CLUB_NAMES,
    _HEARTBEAT_RE,
    LogTailer,
    _estimate_carry,
    _estimate_side,
    parse_club,
    parse_shot,
    parse_status,
)


# ── Fixtures: real lines sampled from Player.log ──────────────────────────────

# All-valid shot: 7-iron (club_num 9)
SHOT_LINE_NORMAL = (
    "Recv Shot Data : 21.65(True), 24.79, 0.13, 4538(True), "
    "5.57(True), 4517(True), 441(True)"
)

# High ball speed shot
SHOT_LINE_HIGH_SPEED = (
    "Recv Shot Data : 49.1(True), 13.7, -0.18, 5416(True), "
    "0.55(True), 5416(True), 52(True)"
)

# Negative carry / side values (pulled/hooked shot)
SHOT_LINE_NEGATIVE_VALUES = (
    "Recv Shot Data : 27.66(True), 4.43, 19.64, 5908(True), "
    "-29.27(True), 5154(True), -2888(True)"
)

# All-valid club data
CLUB_LINE_NORMAL = (
    "Recv Club Data : 4.37(True), -0.92(True), -6.55(True), 33.84(True)"
)

# All-invalid club data (sensor failure / -0.01 sentinel)
CLUB_LINE_ALL_FALSE = (
    "Recv Club Data : -0.01(False), -0.01(False), -0.01(False), -0.01(False)"
)

# Club data with positive face/path (push)
CLUB_LINE_PUSH = (
    "Recv Club Data : 12.92(True), 6.21(True), -6.64(True), 33.36(True)"
)

# Status: right-handed, 8-iron (club_num 9)
STATUS_LINE_7I_RIGHT = (
    " RX : GetStatus Ready, club_sel 6, club_num 9, handed 0, sensor 1"
)

# Status: right-handed, 5-iron (club_num 6)
STATUS_LINE_4I_RIGHT = (
    " RX : GetStatus Detect, club_sel 6, club_num 6, handed 0, sensor 1"
)

# Status: left-handed
STATUS_LINE_LEFT = (
    " RX : GetStatus Ready, club_sel 3, club_num 1, handed 1, sensor 1"
)

# Heartbeat lines
HEARTBEAT_LINE       = "710000000000378ed8 - 4,751.630"
HEARTBEAT_LINE_BARE  = "710000000000378ef0"

# Lines that must NOT match any parser
NON_MATCHING_LINES = [
    "",
    "   ",
    "SQGConnector::OnCharacteristicValueChanged({86602102-6B7E-439A-BDD1-489A3213E9BB}), 1103b6030306090001 - 4,750.564",
    " RX(EVT): 03B6030306090001, rx_id: 77",
    " TX(CMD): 830A000000000000, tx_id: 1291",
    "Enter State:: ShotDone",
    "Enter State:: SetPlayer",
    " RX: ReadyStatus : NoFound, 0, 0, 0",
    " RX: ReadyStatus : BallReady, 1, -48.67, -47.48",
    "[##] Shot:: 21.65, 24.79, 0.13, 4538, 5.57, 4517, 441",
    "[##] After Bonus:: 21.65, 24.79, 0.13, 4538, 5.57, 4517, 441",
    "@@ RX : ShotData  - 4,777.431",
    "@@ HG_Get_ClubData - 4,777.433",
    # Partial / truncated versions
    "Recv Shot Data : 21.65(True), 24.79",
    "Recv Club Data : 4.37(True)",
]


# ── parse_shot ────────────────────────────────────────────────────────────────

class TestParseShot:

    def test_normal_shot_returns_dict(self):
        result = parse_shot(SHOT_LINE_NORMAL)
        assert result is not None

    def test_normal_shot_values(self):
        r = parse_shot(SHOT_LINE_NORMAL)
        assert r["ball_speed"]   == pytest.approx(21.65)
        assert r["launch_angle"] == pytest.approx(24.79)
        assert r["side_angle"]   == pytest.approx(0.13)
        assert r["backspin"]     == 4538
        assert r["carry"]        == pytest.approx(5.57)
        assert r["total_dist"]   == 4517
        assert r["side_dist"]    == pytest.approx(441.0)

    def test_normal_shot_all_valid(self):
        r = parse_shot(SHOT_LINE_NORMAL)
        assert r["ball_speed_valid"]  is True
        assert r["backspin_valid"]    is True
        assert r["carry_valid"]       is True
        assert r["total_dist_valid"]  is True
        assert r["side_dist_valid"]   is True

    def test_high_speed_shot(self):
        r = parse_shot(SHOT_LINE_HIGH_SPEED)
        assert r["ball_speed"]   == pytest.approx(49.1)
        assert r["launch_angle"] == pytest.approx(13.7)
        assert r["side_angle"]   == pytest.approx(-0.18)
        assert r["backspin"]     == 5416
        assert r["total_dist"]   == 5416

    def test_negative_carry_and_side(self):
        r = parse_shot(SHOT_LINE_NEGATIVE_VALUES)
        assert r is not None
        assert r["carry"]     == pytest.approx(-29.27)
        assert r["side_dist"] == pytest.approx(-2888.0)

    def test_negative_side_angle(self):
        r = parse_shot(SHOT_LINE_HIGH_SPEED)
        assert r["side_angle"] == pytest.approx(-0.18)

    def test_returns_none_for_non_matching_lines(self):
        for line in NON_MATCHING_LINES:
            assert parse_shot(line) is None, f"Expected None for: {line!r}"

    def test_returns_none_for_club_data_line(self):
        assert parse_shot(CLUB_LINE_NORMAL) is None

    def test_returns_none_for_status_line(self):
        assert parse_shot(STATUS_LINE_7I_RIGHT) is None

    def test_returns_none_for_heartbeat(self):
        assert parse_shot(HEARTBEAT_LINE) is None

    def test_result_has_all_expected_keys(self):
        r = parse_shot(SHOT_LINE_NORMAL)
        expected_keys = {
            "ball_speed", "ball_speed_valid",
            "launch_angle", "side_angle",
            "backspin", "backspin_valid",
            "carry", "carry_valid",
            "total_dist", "total_dist_valid",
            "side_dist", "side_dist_valid",
        }
        assert set(r.keys()) == expected_keys

    def test_ball_speed_is_float(self):
        r = parse_shot(SHOT_LINE_NORMAL)
        assert isinstance(r["ball_speed"], float)

    def test_backspin_is_int(self):
        r = parse_shot(SHOT_LINE_NORMAL)
        assert isinstance(r["backspin"], int)

    def test_total_dist_is_int(self):
        r = parse_shot(SHOT_LINE_NORMAL)
        assert isinstance(r["total_dist"], int)

    def test_validity_flags_are_bool(self):
        r = parse_shot(SHOT_LINE_NORMAL)
        assert isinstance(r["ball_speed_valid"], bool)
        assert isinstance(r["backspin_valid"], bool)
        assert isinstance(r["carry_valid"], bool)
        assert isinstance(r["total_dist_valid"], bool)
        assert isinstance(r["side_dist_valid"], bool)

    def test_embedded_in_longer_line(self):
        """Parser uses search(), so it should find the data mid-line."""
        prefix = "SomePrefix:: "
        r = parse_shot(prefix + SHOT_LINE_NORMAL)
        assert r is not None
        assert r["ball_speed"] == pytest.approx(21.65)

    @pytest.mark.parametrize("line,expected_speed", [
        ("Recv Shot Data : 21.65(True), 24.79, 0.13, 4538(True), 5.57(True), 4517(True), 441(True)", 21.65),
        ("Recv Shot Data : 49.1(True), 13.7, -0.18, 5416(True), 0.55(True), 5416(True), 52(True)", 49.1),
        ("Recv Shot Data : 30.57(True), 33.08, 1.92, 6549(True), 4.01(True), 6533(True), 458(True)", 30.57),
        ("Recv Shot Data : 15.7(True), 32.47, 2.8, 3052(True), 7.63(True), 3025(True), 405(True)", 15.7),
        ("Recv Shot Data : 17(True), 32.58, 1.76, 2730(True), 13.83(True), 2650(True), 652(True)", 17.0),
        ("Recv Shot Data : 41.23(True), 22.62, -5.42, 8379(True), -10.01(True), 8252(True), -1457(True)", 41.23),
    ])
    def test_parametrized_real_shots(self, line, expected_speed):
        r = parse_shot(line)
        assert r is not None
        assert r["ball_speed"] == pytest.approx(expected_speed)


# ── parse_club ────────────────────────────────────────────────────────────────

class TestParseClub:

    def test_normal_club_returns_dict(self):
        assert parse_club(CLUB_LINE_NORMAL) is not None

    def test_normal_club_values(self):
        r = parse_club(CLUB_LINE_NORMAL)
        assert r["club_speed"]   == pytest.approx(4.37)
        assert r["attack_angle"] == pytest.approx(-0.92)
        assert r["club_path"]    == pytest.approx(-6.55)
        assert r["face_angle"]   == pytest.approx(33.84)

    def test_normal_club_all_valid(self):
        r = parse_club(CLUB_LINE_NORMAL)
        assert r["club_speed_valid"]   is True
        assert r["attack_angle_valid"] is True
        assert r["club_path_valid"]    is True
        assert r["face_angle_valid"]   is True

    def test_all_false_validity(self):
        r = parse_club(CLUB_LINE_ALL_FALSE)
        assert r is not None
        assert r["club_speed_valid"]   is False
        assert r["attack_angle_valid"] is False
        assert r["club_path_valid"]    is False
        assert r["face_angle_valid"]   is False

    def test_all_false_sentinel_values(self):
        r = parse_club(CLUB_LINE_ALL_FALSE)
        assert r["club_speed"]   == pytest.approx(-0.01)
        assert r["attack_angle"] == pytest.approx(-0.01)
        assert r["club_path"]    == pytest.approx(-0.01)
        assert r["face_angle"]   == pytest.approx(-0.01)

    def test_push_shot_positive_face(self):
        r = parse_club(CLUB_LINE_PUSH)
        assert r["club_speed"]   == pytest.approx(12.92)
        assert r["attack_angle"] == pytest.approx(6.21)
        assert r["club_path"]    == pytest.approx(-6.64)
        assert r["face_angle"]   == pytest.approx(33.36)

    def test_returns_none_for_non_matching_lines(self):
        for line in NON_MATCHING_LINES:
            assert parse_club(line) is None, f"Expected None for: {line!r}"

    def test_returns_none_for_shot_data_line(self):
        assert parse_club(SHOT_LINE_NORMAL) is None

    def test_returns_none_for_status_line(self):
        assert parse_club(STATUS_LINE_7I_RIGHT) is None

    def test_result_has_all_expected_keys(self):
        r = parse_club(CLUB_LINE_NORMAL)
        expected_keys = {
            "club_speed", "club_speed_valid",
            "attack_angle", "attack_angle_valid",
            "club_path", "club_path_valid",
            "face_angle", "face_angle_valid",
        }
        assert set(r.keys()) == expected_keys

    def test_all_values_are_float(self):
        r = parse_club(CLUB_LINE_NORMAL)
        for key in ("club_speed", "attack_angle", "club_path", "face_angle"):
            assert isinstance(r[key], float), f"{key} should be float"

    def test_all_valid_flags_are_bool(self):
        r = parse_club(CLUB_LINE_NORMAL)
        for key in ("club_speed_valid", "attack_angle_valid", "club_path_valid", "face_angle_valid"):
            assert isinstance(r[key], bool), f"{key} should be bool"

    @pytest.mark.parametrize("line,expected_speed,expected_valid", [
        ("Recv Club Data : 4.37(True), -0.92(True), -6.55(True), 33.84(True)",    4.37,  True),
        ("Recv Club Data : 0.6(True), -0.38(True), -6.43(True), 20.18(True)",     0.6,   True),
        ("Recv Club Data : 4.17(True), 1.35(True), -6.33(True), 44.76(True)",     4.17,  True),
        ("Recv Club Data : -0.01(False), -0.01(False), -0.01(False), -0.01(False)", -0.01, False),
        ("Recv Club Data : -8.68(True), -4.6(True), -6.42(True), 31.64(True)",   -8.68, True),
        ("Recv Club Data : 12.27(True), -3.51(True), -6.47(True), 34.12(True)",  12.27, True),
    ])
    def test_parametrized_real_club_data(self, line, expected_speed, expected_valid):
        r = parse_club(line)
        assert r is not None
        assert r["club_speed"]       == pytest.approx(expected_speed)
        assert r["club_speed_valid"] is expected_valid


# ── parse_status ──────────────────────────────────────────────────────────────

class TestParseStatus:

    def test_7iron_right_handed(self):
        r = parse_status(STATUS_LINE_7I_RIGHT)
        assert r is not None
        assert r["club_sel"] == 6
        assert r["club_num"] == 9
        assert r["handed"]   == "Right"

    def test_4iron_right_handed(self):
        r = parse_status(STATUS_LINE_4I_RIGHT)
        assert r["club_sel"] == 6
        assert r["club_num"] == 6
        assert r["handed"]   == "Right"

    def test_left_handed(self):
        r = parse_status(STATUS_LINE_LEFT)
        assert r["handed"] == "Left"

    def test_club_sel_maps_to_driver(self):
        line = " RX : GetStatus Ready, club_sel 1, club_num 1, handed 0, sensor 1"
        r = parse_status(line)
        assert r["club_sel"] == 1
        assert CLUB_NAMES[r["club_sel"]] == "Driver"

    def test_club_sel_maps_to_putter(self):
        line = " RX : GetStatus Ready, club_sel 7, club_num 15, handed 0, sensor 1"
        r = parse_status(line)
        assert r["club_sel"] == 7
        assert CLUB_NAMES[r["club_sel"]] == "Putter"

    def test_club_sel_6_maps_to_sw(self):
        """club_sel=6 confirmed by user to be Sand Wedge."""
        line = " RX : GetStatus Ready, club_sel 6, club_num 9, handed 0, sensor 1"
        r = parse_status(line)
        assert r["club_sel"] == 6
        assert CLUB_NAMES[r["club_sel"]] == "SW"

    def test_all_getstatus_states_match(self):
        """Parser must match regardless of state word (Detect/Ready/None)."""
        for state in ("None", "Detect", "Ready", "ShotDone"):
            line = f" RX : GetStatus {state}, club_sel 6, club_num 9, handed 0, sensor 1"
            r = parse_status(line)
            assert r is not None, f"Failed for state: {state}"

    def test_returns_none_for_non_matching_lines(self):
        for line in NON_MATCHING_LINES:
            assert parse_status(line) is None, f"Expected None for: {line!r}"

    def test_returns_none_for_shot_line(self):
        assert parse_status(SHOT_LINE_NORMAL) is None

    def test_returns_none_for_club_line(self):
        assert parse_status(CLUB_LINE_NORMAL) is None

    def test_result_has_all_expected_keys(self):
        r = parse_status(STATUS_LINE_7I_RIGHT)
        assert set(r.keys()) == {"club_sel", "club_num", "handed"}

    def test_club_sel_and_num_are_ints(self):
        r = parse_status(STATUS_LINE_7I_RIGHT)
        assert isinstance(r["club_sel"], int)
        assert isinstance(r["club_num"], int)

    @pytest.mark.parametrize("handed_val,expected", [("0", "Right"), ("1", "Left")])
    def test_handed_values(self, handed_val, expected):
        line = f" RX : GetStatus Ready, club_sel 1, club_num 1, handed {handed_val}, sensor 1"
        r = parse_status(line)
        assert r["handed"] == expected


# ── Heartbeat ─────────────────────────────────────────────────────────────────

class TestHeartbeat:

    def test_standard_heartbeat_matches(self):
        assert _HEARTBEAT_RE.search(HEARTBEAT_LINE) is not None

    def test_bare_heartbeat_matches(self):
        assert _HEARTBEAT_RE.search(HEARTBEAT_LINE_BARE) is not None

    def test_various_heartbeat_suffixes(self):
        for suffix in ["378ed8", "378ef0", "378ef6", "378f02", "378f17", "ffffffff"]:
            line = f"710000000000{suffix}"
            assert _HEARTBEAT_RE.search(line) is not None, f"No match for: {line}"

    def test_shot_line_not_heartbeat(self):
        assert _HEARTBEAT_RE.search(SHOT_LINE_NORMAL) is None

    def test_club_line_not_heartbeat(self):
        assert _HEARTBEAT_RE.search(CLUB_LINE_NORMAL) is None

    def test_status_line_not_heartbeat(self):
        assert _HEARTBEAT_RE.search(STATUS_LINE_7I_RIGHT) is None

    def test_heartbeat_must_start_line(self):
        """The regex is anchored to ^ so a mid-line match must fail."""
        not_heartbeat = " 710000000000378ed8"  # leading space
        assert _HEARTBEAT_RE.search(not_heartbeat) is None


# ── LogTailer._process dispatch ───────────────────────────────────────────────

class TestLogTailerProcess:
    """Unit-tests for _process() without spawning a real thread."""

    def _make_tailer(self):
        q = Queue()
        tailer = LogTailer.__new__(LogTailer)
        tailer.path = "dummy.log"
        tailer.queue = q
        tailer._stop = threading.Event()
        tailer._thread = None
        return tailer, q

    def _drain(self, q):
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        return events

    def test_heartbeat_line_emits_heartbeat_event(self):
        tailer, q = self._make_tailer()
        tailer._process(HEARTBEAT_LINE)
        events = self._drain(q)
        assert ("heartbeat", None) in events

    def test_heartbeat_stops_processing(self):
        """After a heartbeat, shot/club parsers should not also fire."""
        tailer, q = self._make_tailer()
        tailer._process(HEARTBEAT_LINE)
        events = self._drain(q)
        types = [e[0] for e in events]
        assert "shot_data" not in types
        assert "club_data" not in types

    def test_shot_line_emits_shot_data_event(self):
        tailer, q = self._make_tailer()
        tailer._process(SHOT_LINE_NORMAL)
        events = self._drain(q)
        types = [e[0] for e in events]
        assert "shot_data" in types

    def test_shot_event_contains_parsed_dict(self):
        tailer, q = self._make_tailer()
        tailer._process(SHOT_LINE_NORMAL)
        events = self._drain(q)
        shot_events = [e for e in events if e[0] == "shot_data"]
        assert len(shot_events) == 1
        data = shot_events[0][1]
        assert data["ball_speed"] == pytest.approx(21.65)

    def test_club_line_emits_club_data_event(self):
        tailer, q = self._make_tailer()
        tailer._process(CLUB_LINE_NORMAL)
        events = self._drain(q)
        types = [e[0] for e in events]
        assert "club_data" in types

    def test_club_event_contains_parsed_dict(self):
        tailer, q = self._make_tailer()
        tailer._process(CLUB_LINE_NORMAL)
        events = self._drain(q)
        club_events = [e for e in events if e[0] == "club_data"]
        data = club_events[0][1]
        assert data["club_speed"] == pytest.approx(4.37)

    def test_status_line_emits_status_data_event(self):
        tailer, q = self._make_tailer()
        tailer._process(STATUS_LINE_7I_RIGHT)
        events = self._drain(q)
        types = [e[0] for e in events]
        assert "status_data" in types

    def test_unrecognised_line_emits_nothing(self):
        tailer, q = self._make_tailer()
        tailer._process("[##] Shot:: 21.65, 24.79, 0.13, 4538, 5.57, 4517, 441")
        events = self._drain(q)
        assert events == []

    def test_empty_line_emits_nothing(self):
        tailer, q = self._make_tailer()
        tailer._process("")
        events = self._drain(q)
        assert events == []

    def test_full_shot_sequence(self):
        """Simulate a full shot: status → heartbeat → shot → club."""
        tailer, q = self._make_tailer()
        tailer._process(STATUS_LINE_7I_RIGHT)
        tailer._process(HEARTBEAT_LINE)
        tailer._process(SHOT_LINE_NORMAL)
        tailer._process(CLUB_LINE_NORMAL)
        events = self._drain(q)
        types = [e[0] for e in events]
        assert "status_data" in types
        assert "heartbeat"   in types
        assert "shot_data"   in types
        assert "club_data"   in types


# ── LogTailer null-byte stripping ─────────────────────────────────────────────

class TestLogTailerNullStripping:
    """
    Verifies that the tailer correctly reads through a null-padded file
    (matching the actual Player.log format) and emits shot events.
    """

    def _write_log(self, path, content: bytes):
        with open(path, "wb") as f:
            f.write(content)

    def _append_log(self, path, content: bytes):
        with open(path, "ab") as f:
            f.write(content)

    def test_new_lines_appended_after_start(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as tmp:
            path = tmp.name

        try:
            # Write existing content (tailer will seek to end and ignore this)
            self._write_log(path, b"old line\n")

            q = Queue()
            tailer = LogTailer(path, q)
            tailer.start()

            # Give tailer time to open and seek to end
            time.sleep(0.3)

            # Append a shot line
            shot_bytes = (SHOT_LINE_NORMAL + "\n").encode("utf-8")
            self._append_log(path, shot_bytes)

            # Wait for event
            deadline = time.time() + 3.0
            found = False
            while time.time() < deadline:
                while not q.empty():
                    event, data = q.get_nowait()
                    if event == "shot_data":
                        found = True
                        assert data["ball_speed"] == pytest.approx(21.65)
                if found:
                    break
                time.sleep(0.1)

            assert found, "Timed out waiting for shot_data event"
        finally:
            tailer.stop()
            if tailer._thread:
                tailer._thread.join(timeout=2.0)
            os.unlink(path)

    def test_null_padded_file_with_appended_shot(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as tmp:
            path = tmp.name

        try:
            # Mimic the real Player.log: 1 MB of nulls, then some old content
            null_header = b"\x00" * (1024 * 1024)
            old_content = b" TX(CMD): 830A000000000000, tx_id: 1291\r\n"
            self._write_log(path, null_header + old_content)

            q = Queue()
            tailer = LogTailer(path, q)
            tailer.start()
            time.sleep(0.3)

            # Append club line (comes after shot in the real log)
            shot_bytes  = (SHOT_LINE_NORMAL  + "\r\n").encode("utf-8")
            club_bytes  = (CLUB_LINE_NORMAL  + "\r\n").encode("utf-8")
            self._append_log(path, shot_bytes + club_bytes)

            deadline = time.time() + 3.0
            got_shot = False
            got_club = False
            while time.time() < deadline:
                while not q.empty():
                    event, data = q.get_nowait()
                    if event == "shot_data":
                        got_shot = True
                    if event == "club_data":
                        got_club = True
                if got_shot and got_club:
                    break
                time.sleep(0.1)

            assert got_shot, "Did not receive shot_data event"
            assert got_club, "Did not receive club_data event"
        finally:
            tailer.stop()
            if tailer._thread:
                tailer._thread.join(timeout=2.0)
            os.unlink(path)

    def test_file_not_found_retries(self):
        """Tailer should emit a status message and keep retrying on missing file."""
        q = Queue()
        tailer = LogTailer("/nonexistent/path/Player.log", q)
        tailer.start()

        deadline = time.time() + 4.0
        got_not_found = False
        while time.time() < deadline:
            while not q.empty():
                event, data = q.get_nowait()
                if event == "status" and "not found" in data.lower():
                    got_not_found = True
            if got_not_found:
                break
            time.sleep(0.1)

        tailer.stop()
        assert got_not_found, "Expected a 'file not found' status message"


# ── CLUB_NAMES mapping ────────────────────────────────────────────────────────

class TestClubNames:

    def test_seven_clubs_defined(self):
        assert len(CLUB_NAMES) == 7

    def test_club_1_is_driver(self):
        assert CLUB_NAMES[1] == "Driver"

    def test_club_7_is_putter(self):
        assert CLUB_NAMES[7] == "Putter"

    def test_club_6_is_sw(self):
        """club_sel=6 confirmed by user to be Sand Wedge."""
        assert CLUB_NAMES[6] == "SW"

    def test_club_5_is_9iron(self):
        assert CLUB_NAMES[5] == "9-Iron"

    def test_club_4_is_7iron(self):
        assert CLUB_NAMES[4] == "7-Iron"

    @pytest.mark.parametrize("num", range(1, 8))
    def test_all_club_numbers_have_names(self, num):
        assert num in CLUB_NAMES
        assert isinstance(CLUB_NAMES[num], str)
        assert len(CLUB_NAMES[num]) > 0


# ── _estimate_carry ───────────────────────────────────────────────────────────

class TestEstimateCarry:

    def test_matches_user_reported_example(self):
        """
        User-verified: 21.65 m/s, 24.79°, backspin 4538 rpm → simulator shows 39.9 yds.
        Physics model (drag+lift) is an approximation; allow ±5 yds of the reference.
        """
        carry = _estimate_carry(21.65, 24.79, 4538)
        assert abs(carry - 39.9) < 5.0, f"Expected ~39.9 yds, got {carry}"

    def test_zero_ball_speed_returns_zero(self):
        assert _estimate_carry(0.0, 25.0) == 0.0

    def test_zero_launch_angle_returns_zero(self):
        assert _estimate_carry(40.0, 0.0) == 0.0

    def test_negative_launch_angle_returns_zero(self):
        assert _estimate_carry(40.0, -5.0) == 0.0

    def test_higher_ball_speed_gives_more_carry(self):
        slow = _estimate_carry(20.0, 20.0)
        fast = _estimate_carry(40.0, 20.0)
        assert fast > slow

    def test_optimal_angle_45_gives_max_carry(self):
        """45° should give more carry than 20° or 70° at the same speed."""
        carry_20 = _estimate_carry(40.0, 20.0)
        carry_45 = _estimate_carry(40.0, 45.0)
        carry_70 = _estimate_carry(40.0, 70.0)
        assert carry_45 > carry_20
        assert carry_45 > carry_70

    def test_complementary_angles_both_positive(self):
        """30° and 60° are complementary angles — both should produce positive carry.
        Note: aerodynamic model breaks sin(2θ) symmetry, so carries won't be equal."""
        assert _estimate_carry(40.0, 30.0) > 0.0
        assert _estimate_carry(40.0, 60.0) > 0.0

    def test_returns_float(self):
        assert isinstance(_estimate_carry(30.0, 20.0), float)

    def test_always_non_negative(self):
        for v in [5, 20, 40, 60]:
            for a in [5, 15, 30, 45, 60]:
                assert _estimate_carry(v, a) >= 0.0

    def test_matches_fast_shot_verified_example(self):
        """
        Issue #8: User-verified: 46.98 m/s (105.1 mph), 15.23° → simulator shows 145.4 yds.
        Using estimated backspin of 5800 rpm (similar to 5416 rpm observed at 49.1 m/s).
        Physics model is an approximation; allow ±5 yds of the reference.
        """
        carry = _estimate_carry(46.98, 15.23, 5800)
        assert abs(carry - 145.4) < 5.0, f"Expected ~145.4 yds, got {carry}"

    @pytest.mark.parametrize("speed,angle,min_yds,max_yds", [
        # chip: slow speed, high loft
        (10.0, 30.0,  5,  30),
        # mid-iron: 68 mph ball speed
        (30.0, 20.0, 60, 130),
        # driver: 110 mph ball speed
        (49.1, 13.7, 100, 180),
    ])
    def test_carry_in_plausible_range(self, speed, angle, min_yds, max_yds):
        carry = _estimate_carry(speed, angle)
        assert min_yds <= carry <= max_yds, \
            f"v={speed} m/s angle={angle}° → {carry} yds outside [{min_yds}, {max_yds}]"


# ── _estimate_side ────────────────────────────────────────────────────────────

class TestEstimateSide:

    def test_zero_side_angle_returns_zero(self):
        assert _estimate_side(100.0, 0.0) == 0.0

    def test_positive_angle_gives_positive_side(self):
        """Positive side angle → ball goes right → positive offset."""
        assert _estimate_side(100.0, 5.0) > 0.0

    def test_negative_angle_gives_negative_side(self):
        """Negative side angle → ball goes left → negative offset."""
        assert _estimate_side(100.0, -5.0) < 0.0

    def test_larger_carry_gives_larger_offset(self):
        """Same angle, more carry → more lateral displacement."""
        short = _estimate_side(50.0, 10.0)
        long_ = _estimate_side(150.0, 10.0)
        assert long_ > short

    def test_zero_carry_gives_zero_side(self):
        assert _estimate_side(0.0, 10.0) == 0.0

    def test_returns_float(self):
        assert isinstance(_estimate_side(100.0, 5.0), float)

    def test_fat_shot_nearly_straight(self):
        """0.13° side angle on a ~40 yd shot should be < 0.2 yds offline."""
        carry = _estimate_carry(21.65, 24.79)
        side  = _estimate_side(carry, 0.13)
        assert abs(side) < 0.2
