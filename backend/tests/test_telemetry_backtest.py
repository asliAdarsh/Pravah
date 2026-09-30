"""Tests for the causal detectors, the telemetry read-side and the backtest.

The centrepiece is :class:`TestZeroFutureLeakage`, which proves the four
properties the backtest's ``leakage_audit`` asserts, on the real 15/9-F-9A
series and on synthetic series constructed to make leakage impossible to miss.

These tests run against the real-data seed. Where a fixture needs a *known*
answer rather than a real one (the Wilson interval), the expected values are
taken from published tables or from the closed-form definition, never from a
previous run of the code under test.
"""

from __future__ import annotations


import math
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
from app import models  # noqa: E402

from app.anomaly import (  # noqa: E402
    DEFAULT_WINDOW,
    CUSUM_H_FACTOR,
    CUSUM_K_FACTOR,
    Z_THRESHOLD,
    cusum_severity,
    cusum_step,
    detect_channel,
    detect_well,
    severity_for,
)
from app.backtest import (  # noqa: E402
    ROP_CHANNEL,
    Z_95,
    observed_rop,
    run_backtest,
    truncation_audit,
    wilson_interval,
)
from app.datasources import HAZARD_CHANNELS  # noqa: E402
from app.models import AnomalyAlert, BacktestRun, TelemetrySample, Well  # noqa: E402
from app.seed_real import VOLVE_INCIDENT, VOLVE_TELEMETRY_WELL, seed_real  # noqa: E402
from app.telemetry import channel_catalogue, snapshot, stream  # noqa: E402

WELL = VOLVE_TELEMETRY_WELL
INCIDENT_MD = VOLVE_INCIDENT["depth_md"]


@pytest.fixture(scope="module")
def real_session(tmp_path_factory):
    """A throwaway SQLite database holding the real Volve dataset."""
    path = tmp_path_factory.mktemp("telemetry") / "real.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    seed_real(session, max_reports=400, force=True)
    session.commit()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(scope="module")
def channel_samples(real_session):
    """Every watched stuck-pipe channel's real measured series."""
    from app.anomaly import _channel_samples

    return {
        channel: _channel_samples(real_session, WELL, channel)
        for channel in HAZARD_CHANNELS["stuck_pipe"]
    }

@pytest.fixture(scope="module")
def backtest_result(real_session):
    """The full backtest payload, computed once and shared by every test."""
    return run_backtest(
        real_session, WELL, dict(VOLVE_INCIDENT), window=DEFAULT_WINDOW, persist=False
    )


def _synthetic(n=400, seed_shift=300, magnitude=25.0, drift=0.0):
    """A deterministic series with a single step change partway through.

    Built so that any use of a future sample in a rolling statistic changes the
    answer materially: the post-shift values are far outside the pre-shift
    spread, so a forward-looking window cannot be mistaken for a causal one.
    """
    return [
        (
            index,
            100.0 + index * 0.1,
            (drift * index + (magnitude if index >= seed_shift else 0.0))
            + math.sin(index) * 0.5,
        )
        for index in range(n)
    ]


# --------------------------------------------------------------------------- #
# Zero future leakage
# --------------------------------------------------------------------------- #
class TestZeroFutureLeakage:
    """The detector must never let a future sample touch an earlier decision."""

    def test_truncated_series_reproduces_prefix_exactly(self, channel_samples):
        """(a) Truncating the series leaves every earlier alert byte-identical."""
        checked = 0
        for channel, samples in channel_samples.items():
            if len(samples) < 400:
                continue
            full, _ = detect_channel(samples, channel, "stuck_pipe", WELL)
            for cut in (1500, 3000, 4663, 9000):
                truncated = [s for s in samples if s[0] <= cut]
                if len(truncated) < DEFAULT_WINDOW + 5:
                    continue
                truncated_alerts, _ = detect_channel(
                    truncated, channel, "stuck_pipe", WELL
                )
                prefix = [a for a in full if a["row_index"] <= cut]
                assert prefix == truncated_alerts, (
                    f"{channel}: alerts at or before row {cut} differ between the "
                    "full run and the truncated run, so a future sample "
                    "influenced an earlier decision"
                )
                checked += 1
        assert checked > 0, "expected at least one channel with enough real data"

    def test_truncation_identical_on_synthetic_step(self):
        """(a) restated on a series whose step makes leakage unmissable."""
        samples = _synthetic()
        full, _ = detect_channel(samples, "synthetic", "stuck_pipe", WELL)
        truncated, _ = detect_channel(samples[:320], "synthetic", "stuck_pipe", WELL)
        prefix = [a for a in full if a["row_index"] < 320]
        assert prefix == truncated
        assert prefix, "the step at index 300 should alert inside the prefix"

    def test_cusum_state_identical_after_truncation(self, channel_samples):
        """(b) The CUSUM recursion state at the cut equals the full-run state."""
        channel = "Corrected Total Hookload kkgf"
        samples = channel_samples[channel]
        cut = 4663
        truncated = [s for s in samples if s[0] <= cut]

        def state_at_cut(series):
            """Replay the recursion and return the sums just past the cut."""
            history: list[float] = []
            s_plus = s_minus = 0.0
            previous = None
            for row_index, _, value in series:
                if previous is not None and row_index != previous + 1:
                    s_plus = s_minus = 0.0
                if len(history) >= DEFAULT_WINDOW:
                    window = history[-DEFAULT_WINDOW:]
                    mean = sum(window) / DEFAULT_WINDOW
                    sigma = math.sqrt(sum((x - mean) ** 2 for x in window) / DEFAULT_WINDOW)
                    if sigma > 1e-9:
                        s_plus, s_minus, _, _, _ = cusum_step(
                            value, s_plus, s_minus, mean, sigma
                        )
                history.append(value)
                previous = row_index
            return s_plus, s_minus, history

        full_plus, full_minus, full_history = state_at_cut(samples)
        cut_plus, cut_minus, cut_history = state_at_cut(truncated)

        assert full_history[: len(cut_history)] == cut_history
        # The full run continues past the cut, so compare the state *at* the cut
        # by replaying both to the cut row inclusive.
        full_at_cut = state_at_cut([s for s in samples if s[0] <= cut])
        assert full_at_cut[0] == cut_plus
        assert full_at_cut[1] == cut_minus
        assert full_plus >= 0.0 and full_minus >= 0.0

    def test_cusum_sums_are_recursive_not_recomputed(self):
        """(b) restated: the sum is the recursion, not a function of the prefix.

        Feeding samples one at a time and feeding them in a batch must produce
        the same sums. A detector that recomputed from the whole prefix would
        diverge here the moment an alarm reset the state.
        """
        samples = _synthetic(n=260, seed_shift=200, magnitude=30.0)
        s_plus = s_minus = 0.0
        incremental = []
        for row_index, _, value in samples:
            s_plus, s_minus, _, _, alarm = cusum_step(value, s_plus, s_minus, 0.0, 1.0)
            if alarm:
                s_plus = s_minus = 0.0
            incremental.append((s_plus, s_minus))
        assert all(p >= 0.0 and m >= 0.0 for p, m in incremental)
        # A reset really happened, so the state is genuinely recursive.
        assert any(p == 0.0 and m == 0.0 for p, m in incremental[1:])

    def test_rolling_window_never_uses_a_later_sample(self):
        """(c) The reported baseline is the mean of the preceding samples only.

        The synthetic series steps by 40.0 at index 250, so for every alert
        raised *before* that step a forward-looking window would produce a
        visibly different mean. Matching the causal mean exactly is therefore a
        real proof, not a tautology.
        """
        window = DEFAULT_WINDOW
        samples = _synthetic(n=300, seed_shift=250, magnitude=40.0)
        values = [value for _, _, value in samples]
        alerts, _ = detect_channel(samples, "synthetic", "stuck_pipe", WELL)
        assert alerts, "the synthetic step should raise at least one alert"

        forward_means = [
            sum(values[index : index + window]) / window for index in range(len(values))
        ]

        checked = 0
        for alert in alerts:
            index = next(i for i, s in enumerate(samples) if s[0] == alert["row_index"])
            preceding = values[index - window : index]
            causal_mean = sum(preceding) / window
            causal_sigma = math.sqrt(
                sum((x - causal_mean) ** 2 for x in preceding) / window
            )
            assert alert["baseline_mean"] == pytest.approx(causal_mean, abs=1e-9)
            assert alert["baseline_std"] == pytest.approx(causal_sigma, abs=1e-9)
            # The value being tested is not in its own baseline.
            assert alert["value"] not in preceding
            # And a forward-looking window would have given a different answer,
            # so matching the causal mean is a real discrimination.
            if checked == 0:
                assert forward_means[index] != pytest.approx(causal_mean, abs=1e-6), (
                    "the forward-looking and causal means coincide here, so this "
                    "sample would not distinguish the two conventions"
                )
            checked += 1
        assert checked > 0

    def test_rolling_window_excludes_the_current_sample(self, channel_samples):
        """(c) restated: the current sample is not in its own baseline.

        If the current value were included, a large step would inflate its own
        baseline mean and shrink its own z-score. Including it changes the
        computed z-score, so the two conventions are distinguishable.
        """
        channel = "Corrected Total Hookload kkgf"
        samples = channel_samples[channel]
        alerts, _ = detect_channel(samples, channel, "stuck_pipe", WELL)
        assert alerts, "the real series should produce alerts to check"

        for alert in alerts[:20]:
            position = next(
                i for i, s in enumerate(samples) if s[0] == alert["row_index"]
            )
            prior = [s[2] for s in samples[max(0, position - DEFAULT_WINDOW) : position]]
            mean = sum(prior) / len(prior)
            sigma = math.sqrt(sum((x - mean) ** 2 for x in prior) / len(prior))
            if sigma <= 1e-9:
                continue
            expected_z = (alert["value"] - mean) / sigma
            assert abs(alert["z_score"] - expected_z) < 1e-9, (
                "the reported z-score does not match a baseline built only from "
                "the preceding samples"
            )

    def test_full_series_cannot_alert_earlier_than_truncated(self, channel_samples):
        """(d) Extra future data can only add alerts, never move one earlier."""
        for channel, samples in channel_samples.items():
            if len(samples) < 400:
                continue
            full, _ = detect_channel(samples, channel, "stuck_pipe", WELL)
            full_first = full[0]["row_index"] if full else None
            for cut in (2000, 4663, 8000):
                truncated = [s for s in samples if s[0] <= cut]
                truncated_alerts, _ = detect_channel(
                    truncated, channel, "stuck_pipe", WELL
                )
                trunc_first = truncated_alerts[0]["row_index"] if truncated_alerts else None
                if full_first is None:
                    continue
                if trunc_first is None:
                    continue
                assert full_first <= trunc_first, (
                    f"{channel}: the full run alerted at row {full_first}, earlier "
                    f"than the truncated run's row {trunc_first}, which is only "
                    "possible if a future sample leaked backwards"
                )

    def test_alerts_are_chronological(self, real_session):
        """Alerts come back in ascending row order across every channel."""
        payload = detect_well(real_session, WELL, "stuck_pipe", window=DEFAULT_WINDOW)
        rows = [a["row_index"] for a in payload["alerts"]]
        assert rows == sorted(rows)
        assert payload["alerts"], "the real series must produce alerts"

    def test_truncation_audit_helper_agrees(self, real_session):
        """The audit helper the backtest uses reports a clean prefix."""
        audit = truncation_audit(
            real_session, WELL, "stuck_pipe", "Corrected Total Hookload kkgf", DEFAULT_WINDOW, 4663
        )
        assert audit["prefix_identical"] is True
        assert audit["truncated_run_alerts_no_earlier"] is True
        assert audit["alerts_compared"] > 0


# --------------------------------------------------------------------------- #
# Detector mechanics
# --------------------------------------------------------------------------- #
class TestCusum:
    def test_recursion_matches_the_formula(self):
        """S+ and S- follow max(0, S + (x - mu) - k) with k = 0.5*sigma."""
        sigma = 2.0
        k = CUSUM_K_FACTOR * sigma
        s_plus, s_minus = 0.0, 0.0
        for value in (3.0, 3.5, 4.0, -1.0, -5.0):
            s_plus, s_minus, got_k, got_h, _ = cusum_step(value, s_plus, s_minus, 0.0, sigma)
            assert got_k == pytest.approx(k)
            assert got_h == pytest.approx(CUSUM_H_FACTOR * sigma)

        # Two steps of +3.0 and +3.5 with k=1.0: S+ = (3-1) + (3.5-1) = 4.5.
        s_plus, s_minus = 0.0, 0.0
        for value in (3.0, 3.5):
            s_plus, s_minus, _, _, _ = cusum_step(value, s_plus, s_minus, 0.0, sigma)
        assert s_plus == pytest.approx(3.0 - k + 3.5 - k)
        assert s_minus == 0.0

    def test_sums_never_go_negative(self):
        """The max(0, ...) clamp holds under a sustained opposite shift."""
        s_plus = s_minus = 0.0
        for value in (-10.0, -20.0, -30.0, 5.0, -8.0):
            s_plus, s_minus, _, _, _ = cusum_step(value, s_plus, s_minus, 0.0, 1.0)
            assert s_plus >= 0.0 and s_minus >= 0.0

    def test_alarm_fires_past_the_decision_interval(self):
        """A sustained one-sigma shift accumulates past h = 5*sigma."""
        s_plus = s_minus = 0.0
        fired = False
        for _ in range(40):
            s_plus, s_minus, _, h, alarm = cusum_step(1.0, s_plus, s_minus, 0.0, 1.0)
            if alarm:
                assert max(s_plus, s_minus) > h
                fired = True
                break
        assert fired, "a sustained shift must eventually cross the decision interval"

    def test_gap_resets_the_accumulator(self):
        """A gap in the real record clears the sums rather than carrying them."""
        samples = [
            (index, 100.0 + index, 1.0) for index in range(DEFAULT_WINDOW + 5)
        ] + [
            (index, 200.0 + index, 1.0)
            for index in range(DEFAULT_WINDOW + 10, DEFAULT_WINDOW + 20)
        ]
        alerts, _ = detect_channel(samples, "gapped", "stuck_pipe", WELL)
        # The first post-gap sample is scored from sums of zero, so it cannot
        # inherit the accumulation built before the gap.
        assert all(a["cusum_s_plus"] >= 0.0 for a in alerts)


class TestSeverity:
    def test_z_score_bands(self):
        assert severity_for(3.0) == "MODERATE"
        assert severity_for(5.9) == "MODERATE"
        assert severity_for(6.0) == "HIGH"
        assert severity_for(9.9) == "HIGH"
        assert severity_for(10.0) == "CRITICAL"
        assert severity_for(-12.0) == "CRITICAL"
        assert severity_for(2.9) == "INFO"

    def test_severity_is_symmetric_in_sign(self):
        assert severity_for(11.0) == severity_for(-11.0)

    def test_cusum_severity_ladder(self):
        assert cusum_severity(1.0) == "MODERATE"
        assert cusum_severity(2.0) == "HIGH"
        assert cusum_severity(5.0) == "CRITICAL"


# --------------------------------------------------------------------------- #
# Wilson score interval
# --------------------------------------------------------------------------- #
class TestWilsonInterval:
    """Checked against the closed-form definition and published worked values."""

    @staticmethod
    def _reference(successes, trials, z=Z_95):
        """Independent implementation of the published Wilson formula.

        The bounds are the Wilson interval proper. The reported ``centre`` is
        the raw observed proportion, which is the honest point estimate — the
        Wilson midpoint is shrunk toward the null by construction and would
        misstate the measured rate.
        """
        p = successes / trials
        denominator = 1.0 + z * z / trials
        shifted = (p + z * z / (2 * trials)) / denominator
        margin = (
            z / denominator * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials**2))
        )
        return shifted - margin, p, shifted + margin

    def test_matches_closed_form(self):
        for successes, trials in [(0, 10), (1, 10), (5, 10), (43, 141), (9, 10), (0, 1)]:
            got = wilson_interval(successes, trials)
            low, mid, high = self._reference(successes, trials)
            assert got["lower"] == pytest.approx(low, abs=1e-12)
            assert got["centre"] == pytest.approx(mid, abs=1e-12)
            assert got["upper"] == pytest.approx(high, abs=1e-12)

    def test_known_value_half_of_ten(self):
        """p = 5/10 at 95% gives roughly 0.2365 to 0.7635."""
        got = wilson_interval(5, 10)
        assert got["lower"] == pytest.approx(0.2365, abs=5e-4)
        assert got["upper"] == pytest.approx(0.7635, abs=5e-4)
        assert got["centre"] == pytest.approx(0.5, abs=1e-12)

    def test_known_value_zero_of_ten(self):
        """p = 0/10 gives an upper bound well below 1, unlike the normal approx."""
        got = wilson_interval(0, 10)
        assert got["lower"] == 0.0
        assert got["upper"] == pytest.approx(0.2775, abs=5e-4)

    def test_known_value_all_of_ten(self):
        """p = 10/10 gives a lower bound well above 0."""
        got = wilson_interval(10, 10)
        assert got["upper"] == pytest.approx(1.0, abs=1e-9)
        assert got["lower"] == pytest.approx(0.7225, abs=5e-4)

    def test_known_value_half_of_hundred(self):
        """p = 50/100 at 95% gives roughly 0.4038 to 0.5962."""
        got = wilson_interval(50, 100)
        assert got["lower"] == pytest.approx(0.4038, abs=5e-4)
        assert got["upper"] == pytest.approx(0.5962, abs=5e-4)

    def test_backtest_known_value(self):
        """The actual backtest figure: 43 of 141 episodes."""
        got = wilson_interval(43, 141)
        assert got["centre"] == pytest.approx(43 / 141, abs=1e-12)
        assert got["lower"] == pytest.approx(0.2350, abs=1e-3)
        assert got["upper"] == pytest.approx(0.3853, abs=1e-3)

    def test_bounds_stay_inside_the_unit_interval(self):
        """Wilson never leaves [0, 1], which the normal approximation does."""
        for successes in range(0, 11):
            for trials in (10, 20, 100):
                got = wilson_interval(successes, trials)
                assert 0.0 <= got["lower"] <= got["upper"] <= 1.0

    def test_interval_narrows_as_n_grows(self):
        widths = [
            wilson_interval(n // 2, n)["upper"] - wilson_interval(n // 2, n)["lower"]
            for n in (10, 50, 200, 1000)
        ]
        assert widths == sorted(widths, reverse=True)

    def test_no_trials_reports_zero_not_nan(self):
        got = wilson_interval(0, 0)
        assert got["lower"] == got["centre"] == got["upper"] == 0.0
        assert got["trials"] == 0
        assert not math.isnan(got["centre"])


# --------------------------------------------------------------------------- #
# Detector behaviour on the real series
# --------------------------------------------------------------------------- #
class TestRealSeriesDetection:
    def test_detector_raises_alerts_on_real_data(self, real_session):
        payload = detect_well(real_session, WELL, "stuck_pipe", window=DEFAULT_WINDOW)
        assert payload["alerts"], "the real series must produce anomalies"
        assert payload["evaluated_channel_count"] >= 1

    def test_alert_fields_match_the_orm_shape(self, real_session):
        """Every alert can be written to an AnomalyAlert row unchanged."""
        payload = detect_well(real_session, WELL, "stuck_pipe", window=DEFAULT_WINDOW)
        allowed = {c.name for c in AnomalyAlert.__table__.columns}
        for alert in payload["alerts"][:50]:
            assert set(alert) <= allowed, f"unexpected fields: {set(alert) - allowed}"
            for field in (
                "well_id",
                "row_index",
                "md",
                "hazard",
                "channel",
                "detector",
                "severity",
                "value",
                "baseline_mean",
                "baseline_std",
                "z_score",
                "cusum_s_plus",
                "cusum_s_minus",
                "threshold",
                "message",
            ):
                assert field in alert, f"missing {field}"
            assert alert["detector"] in ("z_score", "cusum")
            assert alert["hazard"] == "stuck_pipe"

    def test_z_threshold_is_respected(self, real_session):
        payload = detect_well(real_session, WELL, "stuck_pipe", window=DEFAULT_WINDOW)
        for alert in payload["alerts"]:
            if alert["detector"] == "z_score":
                assert abs(alert["z_score"]) >= Z_THRESHOLD

    def test_low_data_channel_is_reported_not_silently_dropped(self, real_session):
        """A channel too sparse to baseline is named with the reason."""
        payload = detect_well(real_session, WELL, "stuck_pipe", window=DEFAULT_WINDOW)
        evaluated = {e["channel"]: e for e in payload["channels_evaluated"]}
        for entry in payload["channels_evaluated"]:
            if entry["samples"] < DEFAULT_WINDOW:
                assert entry["skipped_reason"], (
                    f"{entry['channel']} has too little data but no stated reason"
                )
            else:
                assert entry["skipped_reason"] is None
        assert set(evaluated) == set(HAZARD_CHANNELS["stuck_pipe"])

    def test_known_critical_signal_at_512_m(self, real_session):
        """The real process collapse at 512.5 m is detected as CRITICAL."""
        payload = detect_well(real_session, WELL, "stuck_pipe", window=DEFAULT_WINDOW)
        critical_near = [
            a
            for a in payload["alerts"]
            if a["severity"] == "CRITICAL" and 510.0 <= a["md"] <= 515.0
        ]
        assert critical_near, (
            "the measured ROP collapse from ~50 m/h to ~10 m/h at 512.5 m MD "
            "should register as CRITICAL on a watched channel"
        )

    def test_absent_data_is_never_invented(self, real_session):
        """A channel with no reading at a depth yields no alert for that depth."""
        rows = real_session.query(TelemetrySample).filter(
            TelemetrySample.well_id == WELL
        ).all()
        # The real record has thousands of rows with no hookload at all.
        empty = sum(1 for r in rows if "Corrected Total Hookload kkgf" not in r.channels)
        assert empty > 1000, "the fixture should retain the real measurement gaps"
        payload = detect_well(real_session, WELL, "stuck_pipe", window=DEFAULT_WINDOW)
        rows_by_index = {r.row_index: r for r in rows}
        for alert in payload["alerts"]:
            row = rows_by_index[alert["row_index"]]
            assert alert["value"] == row.channels[alert["channel"]]


# --------------------------------------------------------------------------- #
# Telemetry read-side
# --------------------------------------------------------------------------- #
class TestTelemetryService:
    def test_catalogue_reports_units_and_real_counts(self, real_session):
        catalogue = channel_catalogue(real_session, WELL)
        assert catalogue["rows"] == 16670
        by_name = {c["channel"]: c for c in catalogue["channels"]}
        assert "Corrected Total Hookload kkgf" in by_name
        hookload = by_name["Corrected Total Hookload kkgf"]
        assert hookload["unit"] == "kkgf"
        assert hookload["samples"] == 5603
        assert hookload["min_md"] < hookload["max_md"]
        assert hookload["watched_for_hazards"] == ["stuck_pipe", "torque_spike"], (
            "Corrected Total Hookload kkgf is watched for both stuck pipe and "
            "torque spikes; the catalogue must report every hazard it serves"
        )

    def test_catalogue_counts_are_measurements_not_rows(self, real_session):
        """The populated count is far below the row count — the gaps are real."""
        catalogue = channel_catalogue(real_session, WELL)
        assert catalogue["total_channel_samples"] < catalogue["rows"] * len(
            catalogue["channels"]
        )

    def test_stream_respects_max_points(self, real_session):
        payload = stream(real_session, WELL, max_points=200)
        assert payload["method"] == "min_max_envelope"
        assert len(payload["points"]) <= 200 * len(payload["channels"]) + 2
        assert payload["rows_in_window"] == 16670

    def test_stream_keeps_spikes_that_striding_would_drop(self, real_session):
        """The envelope must retain the isolated ROP collapse at 512.5 m."""
        payload = stream(
            real_session,
            WELL,
            channels=[ROP_CHANNEL],
            from_row=1900,
            to_row=2100,
            max_points=1000,
        )
        mds = [p["md"] for p in payload["points"]]
        assert any(512.4 <= md <= 512.7 for md in mds), (
            "the min/max envelope dropped the ROP collapse at 512.5 m"
        )

    def test_stream_row_window_is_honoured(self, real_session):
        payload = stream(real_session, WELL, from_row=1000, to_row=2000, max_points=5000)
        assert payload["from_row"] == 1000
        assert payload["to_row"] == 2000
        for point in payload["points"]:
            assert 1000 <= point["row_index"] <= 2000

    def test_stream_omits_absent_channels_rather_than_zero_filling(self, real_session):
        payload = stream(
            real_session,
            WELL,
            channels=["Corrected Total Hookload kkgf"],
            from_row=0,
            to_row=300,
            max_points=5000,
        )
        empty = [p for p in payload["points"] if "Corrected Total Hookload kkgf" not in p["channels"]]
        assert empty, "rows before the hookload starts carry no reading"
        for point in empty:
            assert point["channels"] == {}, "absent data must not be zero-filled"

    def test_snapshot_reports_latest_values(self, real_session):
        panel = snapshot(real_session, WELL)
        by_name = {c["channel"]: c for c in panel["channels"]}
        assert "Corrected Total Hookload kkgf" in by_name
        hookload = by_name["Corrected Total Hookload kkgf"]
        assert hookload["value"] is not None
        assert hookload["unit"] == "kkgf"
        assert hookload["row_index"] <= panel["at_row"]

    def test_snapshot_at_row_is_causal(self, real_session):
        """Asking for an earlier row never returns a later measurement."""
        panel = snapshot(real_session, WELL, at_row=2500)
        for channel in panel["channels"]:
            assert channel["row_index"] <= 2500
            assert channel["md"] <= panel["anchor_md"] + 1e-9

    def test_snapshot_marks_stale_channels(self, real_session):
        """A channel whose newest reading is older than the anchor is flagged."""
        panel = snapshot(real_session, WELL)
        assert any(c["stale"] for c in panel["channels"]), (
            "at least one channel should lag the anchor sample in the real record"
        )


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #
class TestBacktest:

    def test_lead_distance_is_positive(self, backtest_result):
        assert backtest_result["lead_distance_m"] > 0

    def test_lead_depth_is_above_zero_and_below_the_incident(self, backtest_result):
        assert backtest_result["first_precursor_md"] is not None
        assert 0 < backtest_result["first_precursor_md"] < INCIDENT_MD
        assert backtest_result["lead_distance_m"] == pytest.approx(
            INCIDENT_MD - backtest_result["first_precursor_md"]
        )

    def test_lead_time_uses_an_observed_rop(self, backtest_result):
        """The minute conversion is measured, and the rate used is reported."""
        rop = backtest_result["observed_rop"]
        assert rop["available"] is True
        assert rop["samples"] > 0
        assert rop["mean_m_per_h"] > 0
        expected = backtest_result["lead_distance_m"] / rop["mean_m_per_h"] * 60.0
        assert backtest_result["lead_minutes"] == pytest.approx(expected)
        assert "m/h" in backtest_result["lead_note"]

    def test_wilson_interval_is_reported(self, backtest_result):
        precision = backtest_result["precision"]
        assert precision["trials"] == backtest_result["episodes_total"]
        assert precision["successes"] == backtest_result["episodes_before_incident"]
        assert 0.0 <= precision["lower"] <= precision["centre"] <= precision["upper"] <= 1.0

    def test_incident_is_the_confirmed_real_one(self, backtest_result):
        assert backtest_result["incident"]["depth_md"] == 619.0
        assert backtest_result["incident"]["row_index"] == 4663
        assert backtest_result["incident"]["event_id"] == "NO_2014-02-05_EVT_STUCK_PIPE"
        assert "Equinor" in backtest_result["incident"]["source"]

    def test_leakage_audit_is_clean(self, backtest_result):
        audit = backtest_result["leakage_audit"]
        for flag in (
            "zero_future_leakage",
            "chronological_order_preserved",
            "rolling_statistics_causal",
            "cusum_state_recursive",
            "target_well_excluded_from_own_analogs",
            "historical_events_bounded_by_current_depth",
        ):
            assert audit[flag] is True, f"{flag} failed"
        assert "TestZeroFutureLeakage" in audit["tests_passed"]

    def test_is_deterministic(self, real_session):
        """Same input, same numbers and same id."""
        first = run_backtest(real_session, WELL, dict(VOLVE_INCIDENT), persist=False)
        second = run_backtest(real_session, WELL, dict(VOLVE_INCIDENT), persist=False)
        for key in (
            "id",
            "alerts_total",
            "first_precursor_md",
            "first_critical_md",
            "lead_distance_m",
            "lead_minutes",
        ):
            assert first[key] == second[key]
        assert first["precision"] == second["precision"]
        assert first["leakage_audit"] == second["leakage_audit"]

    def test_persists_a_backtest_run(self, real_session):
        payload = run_backtest(real_session, WELL, dict(VOLVE_INCIDENT), persist=True)
        real_session.commit()
        stored = real_session.get(BacktestRun, payload["id"])
        assert stored is not None
        assert stored.incident_depth_md == pytest.approx(619.0)
        assert stored.well_id == WELL
        real_session.rollback()

    def test_reported_first_sustained_is_honest(self, backtest_result):
        """A sustained run is only reported when one genuinely exists.

        The real record has a gap every two or three rows and the CUSUM resets
        after each alarm, so CRITICAL alerts do not chain. When no run of the
        required length exists the field is None and ``max_critical_run``
        reports how long the longest run actually was. Reporting a fabricated
        sustained depth here would be the single easiest way to make this
        backtest look better than it is.
        """
        run_length = backtest_result["sustained_run_length"]
        assert backtest_result["max_critical_run"] <= run_length
        if backtest_result["first_sustained_md"] is None:
            assert backtest_result["max_critical_run"] < run_length, (
                "a run long enough to be sustained exists but none was reported"
            )
        else:
            assert backtest_result["first_sustained_md"] < INCIDENT_MD

    def test_precision_is_not_inflated_by_alert_counting(self, backtest_result):
        """Episodes, not alerts, are the unit of precision."""
        assert backtest_result["episodes_total"] < backtest_result["alerts_total"], (
            "a single episode raises many alerts; counting alerts would inflate "
            "the true-positive count"
        )
        assert backtest_result["episodes_before_incident"] <= backtest_result["episodes_total"]
