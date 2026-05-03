"""
CI evals — no Ollama required.

Tests that the prompt template:
  - Renders correctly for every representative shot scenario
  - Contains all required field names
  - Stays within a sensible token budget
  - Correctly reflects shot characteristics (out-to-in, open face, etc.)
  - Handles edge cases (invalid readings, unknown clubs)
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import (
    CLUB_NAMES, DEFAULT_PROMPT,
    _estimate_carry, _estimate_side,
    _fmt_club_path, _fmt_face_angle, _fmt_attack_angle,
)

# ── Representative shot scenarios ─────────────────────────────────────────────
# Each entry is a realistic complete shot dict as produced by _on_complete_shot.

PULL_HOOK = {
    # Out-to-in path, closed face → pull hook
    "club_name": "7-Iron", "handed": "Right",
    "ball_speed": 41.23, "ball_speed_mph": round(41.23 * 2.237, 1), "ball_speed_valid": True,
    "launch_angle": 22.62, "side_angle": -5.42,
    "backspin": 8379, "backspin_valid": True,
    "carry": -10.01, "carry_valid": True,
    "total_dist": 8252, "total_dist_valid": True,
    "side_dist": -1457.0, "side_dist_valid": True,
    "carry_yards": _estimate_carry(41.23, 22.62),
    "side_dist_yards": _estimate_side(_estimate_carry(41.23, 22.62), -5.42),
    "club_speed": -8.68, "club_speed_valid": True,
    "attack_angle": -4.6, "attack_angle_valid": True,
    "club_path": -6.42, "club_path_valid": True,
    "face_angle": 31.64, "face_angle_valid": True,
    "club_path_dir": _fmt_club_path(-8.68, "Right"),
    "face_angle_dir": _fmt_face_angle(-4.6, "Right"),
    "attack_angle_dir": _fmt_attack_angle(-6.42),
}

PUSH_SLICE = {
    # In-to-out path, open face → push slice
    "club_name": "Driver", "handed": "Right",
    "ball_speed": 49.1, "ball_speed_mph": round(49.1 * 2.237, 1), "ball_speed_valid": True,
    "launch_angle": 13.7, "side_angle": 5.5,
    "backspin": 3200, "backspin_valid": True,
    "carry": 12.0, "carry_valid": True,
    "total_dist": 5416, "total_dist_valid": True,
    "side_dist": 1800.0, "side_dist_valid": True,
    "carry_yards": _estimate_carry(49.1, 13.7),
    "side_dist_yards": _estimate_side(_estimate_carry(49.1, 13.7), 5.5),
    "club_speed": 12.92, "club_speed_valid": True,
    "attack_angle": 6.21, "attack_angle_valid": True,
    "club_path": 6.64, "club_path_valid": True,
    "face_angle": 33.36, "face_angle_valid": True,
    "club_path_dir": _fmt_club_path(12.92, "Right"),
    "face_angle_dir": _fmt_face_angle(6.21, "Right"),
    "attack_angle_dir": _fmt_attack_angle(6.64),
}

SOLID_STRIKE = {
    # Square path/face, good numbers → solid iron shot
    "club_name": "5-Iron", "handed": "Right",
    "ball_speed": 38.71, "ball_speed_mph": round(38.71 * 2.237, 1), "ball_speed_valid": True,
    "launch_angle": 23.96, "side_angle": 0.5,
    "backspin": 5800, "backspin_valid": True,
    "carry": 9.55, "carry_valid": True,
    "total_dist": 8026, "total_dist_valid": True,
    "side_dist": 120.0, "side_dist_valid": True,
    "carry_yards": _estimate_carry(38.71, 23.96),
    "side_dist_yards": _estimate_side(_estimate_carry(38.71, 23.96), 0.5),
    "club_speed": 10.5, "club_speed_valid": True,
    "attack_angle": -3.2, "attack_angle_valid": True,
    "club_path": -0.5, "club_path_valid": True,
    "face_angle": 0.8, "face_angle_valid": True,
    "club_path_dir": _fmt_club_path(10.5, "Right"),
    "face_angle_dir": _fmt_face_angle(-3.2, "Right"),
    "attack_angle_dir": _fmt_attack_angle(-0.5),
}

TOPPED_SHOT = {
    # Low ball speed, high backspin anomaly, tiny carry
    "club_name": "5-Iron", "handed": "Right",
    "ball_speed": 15.7, "ball_speed_mph": round(15.7 * 2.237, 1), "ball_speed_valid": True,
    "launch_angle": 2.1, "side_angle": 0.0,
    "backspin": 1200, "backspin_valid": True,
    "carry": 2.5, "carry_valid": True,
    "total_dist": 800, "total_dist_valid": True,
    "side_dist": 50.0, "side_dist_valid": True,
    "carry_yards": _estimate_carry(15.7, 2.1),
    "side_dist_yards": _estimate_side(_estimate_carry(15.7, 2.1), 0.0),
    "club_speed": 9.0, "club_speed_valid": True,
    "attack_angle": 5.5, "attack_angle_valid": True,
    "club_path": -1.0, "club_path_valid": True,
    "face_angle": 2.0, "face_angle_valid": True,
    "club_path_dir": _fmt_club_path(9.0, "Right"),
    "face_angle_dir": _fmt_face_angle(5.5, "Right"),
    "attack_angle_dir": _fmt_attack_angle(-1.0),
}

FAT_SHOT = {
    # Steep attack angle, low ball speed relative to club speed
    "club_name": "7-Iron", "handed": "Right",
    "ball_speed": 21.65, "ball_speed_mph": round(21.65 * 2.237, 1), "ball_speed_valid": True,
    "launch_angle": 24.79, "side_angle": 0.13,
    "backspin": 4538, "backspin_valid": True,
    "carry": 5.57, "carry_valid": True,
    "total_dist": 4517, "total_dist_valid": True,
    "side_dist": 441.0, "side_dist_valid": True,
    "carry_yards": _estimate_carry(21.65, 24.79),
    "side_dist_yards": _estimate_side(_estimate_carry(21.65, 24.79), 0.13),
    "club_speed": 4.37, "club_speed_valid": True,
    "attack_angle": -0.92, "attack_angle_valid": True,
    "club_path": -6.55, "club_path_valid": True,
    "face_angle": 33.84, "face_angle_valid": True,
    "club_path_dir": _fmt_club_path(4.37, "Right"),
    "face_angle_dir": _fmt_face_angle(-0.92, "Right"),
    "attack_angle_dir": _fmt_attack_angle(-6.55),
}

INVALID_CLUB_DATA = {
    # Club sensor failure — all -0.01 / False
    "club_name": "PW", "handed": "Right",
    "ball_speed": 30.34, "ball_speed_mph": round(30.34 * 2.237, 1), "ball_speed_valid": True,
    "launch_angle": 32.41, "side_angle": 0.78,
    "backspin": 5925, "backspin_valid": True,
    "carry": -0.06, "carry_valid": True,
    "total_dist": 5925, "total_dist_valid": True,
    "side_dist": -6.0, "side_dist_valid": True,
    "carry_yards": _estimate_carry(30.34, 32.41),
    "side_dist_yards": _estimate_side(_estimate_carry(30.34, 32.41), 0.78),
    "club_speed": -0.01, "club_speed_valid": False,
    "attack_angle": -0.01, "attack_angle_valid": False,
    "club_path": -0.01, "club_path_valid": False,
    "face_angle": -0.01, "face_angle_valid": False,
    "club_path_dir": _fmt_club_path(-0.01, "Right"),
    "face_angle_dir": _fmt_face_angle(-0.01, "Right"),
    "attack_angle_dir": _fmt_attack_angle(-0.01),
}

LEFT_HANDED = {
    "club_name": "Driver", "handed": "Left",
    "ball_speed": 52.0, "ball_speed_mph": round(52.0 * 2.237, 1), "ball_speed_valid": True,
    "launch_angle": 11.5, "side_angle": 2.1,
    "backspin": 2800, "backspin_valid": True,
    "carry": 8.0, "carry_valid": True,
    "total_dist": 6100, "total_dist_valid": True,
    "side_dist": 300.0, "side_dist_valid": True,
    "carry_yards": _estimate_carry(52.0, 11.5),
    "side_dist_yards": _estimate_side(_estimate_carry(52.0, 11.5), 2.1),
    "club_speed": 15.1, "club_speed_valid": True,
    "attack_angle": -2.0, "attack_angle_valid": True,
    "club_path": 1.5, "club_path_valid": True,
    "face_angle": 3.2, "face_angle_valid": True,
    "club_path_dir": _fmt_club_path(15.1, "Left"),
    "face_angle_dir": _fmt_face_angle(-2.0, "Left"),
    "attack_angle_dir": _fmt_attack_angle(1.5),
}

UNKNOWN_CLUB = {
    # club_num not in CLUB_NAMES → fallback label
    "club_name": "Club #99", "handed": "Right",
    "ball_speed": 25.0, "ball_speed_mph": round(25.0 * 2.237, 1), "ball_speed_valid": True,
    "launch_angle": 20.0, "side_angle": 0.0,
    "backspin": 4000, "backspin_valid": True,
    "carry": 5.0, "carry_valid": True,
    "total_dist": 3500, "total_dist_valid": True,
    "side_dist": 0.0, "side_dist_valid": True,
    "carry_yards": _estimate_carry(25.0, 20.0),
    "side_dist_yards": _estimate_side(_estimate_carry(25.0, 20.0), 0.0),
    "club_speed": 8.0, "club_speed_valid": True,
    "attack_angle": -2.0, "attack_angle_valid": True,
    "club_path": 0.0, "club_path_valid": True,
    "face_angle": 0.0, "face_angle_valid": True,
    "club_path_dir": _fmt_club_path(8.0, "Right"),
    "face_angle_dir": _fmt_face_angle(-2.0, "Right"),
    "attack_angle_dir": _fmt_attack_angle(0.0),
}

ALL_SCENARIOS = [
    ("pull_hook",        PULL_HOOK),
    ("push_slice",       PUSH_SLICE),
    ("solid_strike",     SOLID_STRIKE),
    ("topped_shot",      TOPPED_SHOT),
    ("fat_shot",         FAT_SHOT),
    ("invalid_club_data",INVALID_CLUB_DATA),
    ("left_handed",      LEFT_HANDED),
    ("unknown_club",     UNKNOWN_CLUB),
]

# Required format keys in the default prompt
REQUIRED_KEYS = {
    "club_name", "handed",
    "ball_speed_mph", "launch_angle",
    "side_angle",        # Direction
    "backspin",          # Spin Rate
    "carry",             # Spin Axis
    "total_dist",        # Back Spin component (rpm)
    "side_dist",         # Side Spin component (rpm)
    "carry_yards",       # Estimated carry distance
    "side_dist_yards",   # Estimated side distance
    "club_path_dir",     # Club Path with direction label
    "face_angle_dir",    # Face to Target with direction label
    "attack_angle_dir",  # Attack Angle with direction label
    "face_angle",        # Dynamic Loft (degrees)
}


def render(template: str, data: dict) -> str:
    return template.format(**data)


# ── Prompt structure tests ────────────────────────────────────────────────────

class TestPromptTemplate:

    def test_default_prompt_contains_all_required_keys(self):
        """Every required field name must appear as a {key} in the template."""
        import string
        formatter = string.Formatter()
        keys_in_template = {
            field_name
            for _, field_name, _, _ in formatter.parse(DEFAULT_PROMPT)
            if field_name
        }
        missing = REQUIRED_KEYS - keys_in_template
        assert not missing, f"Missing keys in DEFAULT_PROMPT: {missing}"

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS)
    def test_renders_without_error(self, name, data):
        """Template must render cleanly for every shot scenario."""
        prompt = render(DEFAULT_PROMPT, data)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS)
    def test_rendered_prompt_within_token_budget(self, name, data):
        """Prompt should stay under ~800 words (~1100 tokens) to leave room for response."""
        prompt = render(DEFAULT_PROMPT, data)
        word_count = len(prompt.split())
        assert word_count < 800, f"Prompt for {name!r} is {word_count} words — may exceed context"

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS)
    def test_rendered_prompt_not_too_short(self, name, data):
        """Prompt must contain enough context to be useful (>50 words)."""
        prompt = render(DEFAULT_PROMPT, data)
        word_count = len(prompt.split())
        assert word_count > 50, f"Prompt for {name!r} is suspiciously short: {word_count} words"

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS)
    def test_club_name_present_in_rendered_prompt(self, name, data):
        prompt = render(DEFAULT_PROMPT, data)
        assert data["club_name"] in prompt

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS)
    def test_handed_present_in_rendered_prompt(self, name, data):
        prompt = render(DEFAULT_PROMPT, data)
        assert data["handed"] in prompt

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS)
    def test_numeric_values_appear_in_prompt(self, name, data):
        """Key numeric values must be visibly present in the rendered prompt."""
        prompt = render(DEFAULT_PROMPT, data)
        for key in ("ball_speed_mph", "launch_angle", "backspin"):
            assert str(data[key]) in prompt, \
                f"Expected {key}={data[key]} to appear in prompt for {name!r}"


# ── Prompt content / characteristic tests ────────────────────────────────────

class TestPromptCharacteristics:

    def test_out_to_in_path_reflected_in_prompt(self):
        """Out-to-in club path should appear as direction label, not raw negative."""
        prompt = render(DEFAULT_PROMPT, PULL_HOOK)
        assert "Out-to-In" in prompt

    def test_open_face_reflected_in_prompt(self):
        """Open face should appear as direction label in the prompt."""
        prompt = render(DEFAULT_PROMPT, PUSH_SLICE)
        assert "Open" in prompt

    def test_left_handed_label_in_prompt(self):
        prompt = render(DEFAULT_PROMPT, LEFT_HANDED)
        assert "Left" in prompt

    def test_invalid_club_data_sentinel_in_prompt(self):
        """Sensor-failure values should appear in the prompt (absolute value shown)."""
        prompt = render(DEFAULT_PROMPT, INVALID_CLUB_DATA)
        assert "0.01" in prompt

    def test_unknown_club_label_in_prompt(self):
        prompt = render(DEFAULT_PROMPT, UNKNOWN_CLUB)
        assert "Club #99" in prompt

    def test_prompt_contains_coach_instruction(self):
        """Default prompt must instruct the LLM to act as a golf instructor."""
        prompt = render(DEFAULT_PROMPT, SOLID_STRIKE)
        lower = prompt.lower()
        assert "golf" in lower or "instructor" in lower or "coach" in lower

    def test_prompt_requests_tips_or_feedback(self):
        """Default prompt must ask for actionable output."""
        prompt = render(DEFAULT_PROMPT, SOLID_STRIKE)
        lower = prompt.lower()
        assert any(word in lower for word in ("tip", "feedback", "feel", "improve", "provide"))

    def test_attack_angle_note_present(self):
        """Attack angle direction label should appear in the rendered prompt."""
        prompt = render(DEFAULT_PROMPT, FAT_SHOT)
        # FAT_SHOT club_path=-6.55 → "Descending"
        assert "descending" in prompt.lower() or "ascending" in prompt.lower() or "attack angle" in prompt.lower()

    def test_dynamic_loft_label_present(self):
        """Dynamic Loft label should appear in the rendered prompt."""
        prompt = render(DEFAULT_PROMPT, SOLID_STRIKE)
        assert "dynamic loft" in prompt.lower()


# ── Custom template tests ─────────────────────────────────────────────────────

class TestCustomPromptTemplate:

    MINIMAL_TEMPLATE = (
        "Ball speed: {ball_speed}, club path: {club_path}. "
        "Club: {club_name}. Give one tip."
    )

    MISSING_KEY_TEMPLATE = "Ball speed: {ball_speed}. Club: {club_name}. {nonexistent_key}"

    def test_minimal_template_renders(self):
        prompt = render(self.MINIMAL_TEMPLATE, SOLID_STRIKE)
        assert "38.71" in prompt
        assert "5-Iron" in prompt

    def test_template_missing_key_raises_keyerror(self):
        """If the template references a key not in the shot data, KeyError is raised."""
        with pytest.raises(KeyError):
            render(self.MISSING_KEY_TEMPLATE, SOLID_STRIKE)

    def test_template_with_extra_whitespace_renders(self):
        template = "  {club_name}  |  {ball_speed}  "
        prompt = render(template, SOLID_STRIKE)
        assert "5-Iron" in prompt

    def test_repeated_keys_render_consistently(self):
        template = "{club_name} hit {club_name} with {ball_speed}"
        prompt = render(template, SOLID_STRIKE)
        assert prompt.count("5-Iron") == 2


# ── Response quality criteria (heuristics for evaluating LLM output) ─────────

def score_response(response: str, shot: dict) -> dict:
    """
    Score an LLM response against quality criteria.
    Returns a dict of criterion → bool.
    Used by the live eval script (evals/run_evals.py) and testable in isolation.
    """
    text  = response.strip()
    lower = text.lower()
    words = text.split()

    return {
        # Length: not a refusal, not a wall of text
        "min_length":      len(words) >= 20,
        "max_length":      len(words) <= 400,

        # Actionability: contains action-verb language (not just data description).
        # Use word-boundary matching to avoid substring false positives
        # (e.g. "stance" inside "distance", "aim" inside "claimed").
        "actionable":      bool(re.search(
            r'\b(try|focus|feel|keep|make sure|rotate|shift|swing|grip'
            r'|posture|hinge|turn|extend|release|square|work on'
            r'|should|will help|improve)\b',
            lower
        )),

        # Structure: has multiple tips (numbered list or multiple sentences)
        "has_multiple_tips": (
            any(c in text for c in ("1.", "2.", "•", "-", "\n\n"))
            or text.count(". ") >= 2
        ),

        # Relevance: mentions at least one of the key data dimensions
        "mentions_data_dimension": any(w in lower for w in (
            "ball speed", "launch", "backspin", "spin", "carry",
            "distance", "path", "face", "attack", "club speed",
            "angle", "side", "slice", "hook", "draw", "fade",
        )),

        # No hallucinated units: shouldn't invent mph figures from m/s input
        # (a rough heuristic — flag if a suspiciously large speed appears)
        "no_obvious_hallucination": "999" not in text and "1000 mph" not in lower,
    }


class TestScoreResponse:

    def test_good_response_passes_all_criteria(self):
        response = (
            "1. Your club path is significantly out-to-in (-6.42°) which is causing "
            "the pull. Try to feel like you're swinging more to the right of the target.\n"
            "2. Your face angle is very open (33.84°) relative to path — focus on "
            "rotating your forearms through impact to square the face.\n"
            "3. A steep attack angle combined with this path will add side spin. "
            "Work on a shallower approach to reduce backspin and improve carry distance."
        )
        scores = score_response(response, PULL_HOOK)
        assert all(scores.values()), f"Failed criteria: {[k for k,v in scores.items() if not v]}"

    def test_too_short_response_fails_min_length(self):
        scores = score_response("Try harder.", SOLID_STRIKE)
        assert scores["min_length"] is False

    def test_wall_of_text_fails_max_length(self):
        scores = score_response(" ".join(["word"] * 500), SOLID_STRIKE)
        assert scores["max_length"] is False

    def test_non_actionable_response_flagged(self):
        response = (
            "The data shows a club path of -6.42 and a face angle of 33.84. "
            "The ball speed was 41.23 and the launch angle was 22.62 degrees. "
            "The backspin was 8379 rpm and side distance was -1457."
        )
        scores = score_response(response, PULL_HOOK)
        assert scores["actionable"] is False

    def test_single_sentence_flagged_for_tips(self):
        scores = score_response(
            "Just make sure you keep your head down throughout the swing.", SOLID_STRIKE
        )
        assert scores["has_multiple_tips"] is False

    def test_relevant_response_passes_dimension_check(self):
        scores = score_response(
            "Your club path is too steep. Focus on your face angle at impact.", PULL_HOOK
        )
        assert scores["mentions_data_dimension"] is True

    def test_score_returns_all_expected_keys(self):
        scores = score_response("Some feedback here.", SOLID_STRIKE)
        assert set(scores.keys()) == {
            "min_length", "max_length", "actionable",
            "has_multiple_tips", "mentions_data_dimension",
            "no_obvious_hallucination",
        }

    def test_score_values_are_all_bool(self):
        scores = score_response("Some feedback here.", SOLID_STRIKE)
        for k, v in scores.items():
            assert isinstance(v, bool), f"{k} should be bool"

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS)
    def test_score_runs_without_error_for_all_scenarios(self, name, data):
        """score_response must not raise for any scenario."""
        scores = score_response("Focus on your swing path and face angle.", data)
        assert isinstance(scores, dict)
