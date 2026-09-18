"""Traffic-based mesh gate via OpenHop Adaptive Rate Limiting tiers."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from zorcore.config import Settings
from zorcore.repeater_api import api_headers

logger = logging.getLogger(__name__)

TIER_RANK = {
    "quiet": 0,
    "normal": 1,
    "busy": 2,
    "congested": 3,
}


def normalize_tier(tier: str) -> str:
    return (tier or "").strip().lower()


def tier_rank(tier: str) -> int:
    return TIER_RANK.get(normalize_tier(tier), 99)


def tier_allows_play(current_tier: str, play_max_tier: str) -> bool:
    """True when current advert tier is at or below the configured max."""
    cur = normalize_tier(current_tier)
    if not cur:
        return False
    return tier_rank(cur) <= tier_rank(play_max_tier)


def _as_bool(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
    return None


@dataclass
class QuietSample:
    utilization_percent: float | None = None
    rx_per_hour: float | None = None
    current_airtime_ms: float | None = None
    advert_tier: str = ""
    play_max_tier: str = "normal"
    adaptive_enabled: bool | None = None
    adverts_per_min_ewma: float | None = None
    packets_per_min_ewma: float | None = None
    raw_quiet: bool = False  # alias: raw mesh open (tier ok)
    is_quiet: bool = False  # held open for play (global, not local exception)
    reason: str = "unsampled"
    error: str = ""
    sampled_at: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.is_quiet


@dataclass
class QuietMonitor:
    """Poll Repeater Adaptive Rate Limiting tier and apply hysteresis."""

    settings: Settings
    _quiet_since: float | None = None
    _last: QuietSample = field(default_factory=QuietSample)

    @property
    def sample(self) -> QuietSample:
        return self._last

    @property
    def is_quiet(self) -> bool:
        """Global mesh open (tier within play_max_tier after hold)."""
        if not getattr(self.settings, "safety_enabled", True):
            return True
        return self._last.is_quiet

    @property
    def is_open(self) -> bool:
        return self.is_quiet

    def _get_json(self, path: str) -> dict[str, Any]:
        url = f"{self.settings.repeater_api_base.rstrip('/')}{path}"
        req = urllib.request.Request(url, headers=api_headers(self.settings), method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
        if isinstance(body, dict) and isinstance(body.get("data"), dict):
            return body["data"]
        return body if isinstance(body, dict) else {}

    def _raw_open_from_tier(self, tier: str) -> tuple[bool, str]:
        max_tier = normalize_tier(getattr(self.settings, "play_max_tier", "normal") or "normal")
        if max_tier not in TIER_RANK or max_tier == "congested":
            max_tier = "busy" if max_tier == "congested" else "normal"
        cur = normalize_tier(tier)
        if not cur:
            return False, "no_advert_tier"
        if tier_allows_play(cur, max_tier):
            return True, f"tier {cur} <= max {max_tier}"
        return False, f"tier {cur} > max {max_tier}"

    def poll_once(self) -> QuietSample:
        now = time.time()
        max_tier = normalize_tier(getattr(self.settings, "play_max_tier", "normal") or "normal")
        sample = QuietSample(sampled_at=now, play_max_tier=max_tier)
        try:
            # Telemetry only — not used for the open/closed gate.
            try:
                stats = self._get_json("/api/stats")
            except Exception as exc:
                logger.debug("stats telemetry failed: %s", exc)
                stats = {}
            util = stats.get("utilization_percent")
            if util is None and isinstance(stats.get("airtime"), dict):
                util = stats["airtime"].get("utilization_percent")
            try:
                sample.utilization_percent = float(util) if util is not None else None
            except (TypeError, ValueError):
                sample.utilization_percent = None
            try:
                rx = stats.get("rx_per_hour")
                sample.rx_per_hour = float(rx) if rx is not None else None
            except (TypeError, ValueError):
                sample.rx_per_hour = None
            air = stats.get("current_airtime_ms")
            if air is None and isinstance(stats.get("airtime"), dict):
                air = stats["airtime"].get("current_airtime_ms")
            try:
                sample.current_airtime_ms = float(air) if air is not None else None
            except (TypeError, ValueError):
                sample.current_airtime_ms = None

            tier = ""
            arl_ok = False
            try:
                ar = self._get_json("/api/advert_rate_limit_stats")
                adaptive = ar.get("adaptive") if isinstance(ar.get("adaptive"), dict) else None
                if adaptive is None and "enabled" in ar and "current_tier" in ar:
                    # Flat payload fallback (older helpers).
                    adaptive = ar
                if not isinstance(adaptive, dict):
                    sample.adaptive_enabled = None
                    sample.reason = "arl_unavailable"
                    sample.error = "adaptive_missing"
                    sample.raw_quiet = False
                    sample.is_quiet = False
                    self._quiet_since = None
                    if not getattr(self.settings, "safety_enabled", True):
                        sample.is_quiet = True
                        sample.reason = "safety_disabled"
                    self._last = sample
                    return sample

                enabled = _as_bool(adaptive.get("enabled"))
                sample.adaptive_enabled = enabled
                metrics = ar.get("metrics") if isinstance(ar.get("metrics"), dict) else {}
                try:
                    apm = metrics.get("adverts_per_min_ewma")
                    sample.adverts_per_min_ewma = float(apm) if apm is not None else None
                except (TypeError, ValueError):
                    sample.adverts_per_min_ewma = None
                try:
                    ppm = metrics.get("packets_per_min_ewma")
                    sample.packets_per_min_ewma = float(ppm) if ppm is not None else None
                except (TypeError, ValueError):
                    sample.packets_per_min_ewma = None

                if enabled is not True:
                    # Do not trust a stale tier when Adaptive Rate Limiting is off.
                    sample.advert_tier = ""
                    sample.raw_quiet = False
                    sample.is_quiet = False
                    sample.reason = "arl_disabled"
                    self._quiet_since = None
                    if not getattr(self.settings, "safety_enabled", True):
                        sample.is_quiet = True
                        sample.reason = "safety_disabled"
                    self._last = sample
                    return sample

                arl_ok = True
                tier = str(adaptive.get("current_tier") or ar.get("current_tier") or "")
            except Exception as exc:
                logger.debug("advert_rate_limit_stats failed: %s", exc)
                sample.adaptive_enabled = None
                sample.error = type(exc).__name__
                sample.reason = "arl_unavailable"
                sample.raw_quiet = False
                sample.is_quiet = False
                self._quiet_since = None
                if not getattr(self.settings, "safety_enabled", True):
                    sample.is_quiet = True
                    sample.reason = "safety_disabled"
                self._last = sample
                return sample

            sample.advert_tier = normalize_tier(tier) if arl_ok else ""

            raw_ok, reason = self._raw_open_from_tier(sample.advert_tier)
            sample.raw_quiet = raw_ok
            sample.reason = reason

            hold = float(getattr(self.settings, "quiet_hold_seconds", 120))
            if not raw_ok:
                self._quiet_since = None
                sample.is_quiet = False
            else:
                if self._quiet_since is None:
                    self._quiet_since = now
                elapsed = now - self._quiet_since
                if elapsed >= hold:
                    sample.is_quiet = True
                    sample.reason = f"{reason}; held {int(elapsed)}s"
                else:
                    sample.is_quiet = False
                    sample.reason = f"{reason}; holding {int(elapsed)}/{int(hold)}s"
        except urllib.error.HTTPError as exc:
            sample.error = f"HTTP {exc.code}"
            sample.reason = "arl_unavailable"
            sample.adaptive_enabled = None
            sample.is_quiet = False
            self._quiet_since = None
            logger.warning("quiet poll HTTP error: %s", exc.code)
        except Exception as exc:
            sample.error = type(exc).__name__
            sample.reason = "arl_unavailable"
            sample.adaptive_enabled = None
            sample.is_quiet = False
            self._quiet_since = None
            logger.warning("quiet poll failed: %s", type(exc).__name__)

        if not getattr(self.settings, "safety_enabled", True):
            sample.is_quiet = True
            sample.reason = "safety_disabled"

        self._last = sample
        return sample

    def to_runtime(self) -> dict[str, Any]:
        s = self._last
        return {
            "is_quiet": s.is_quiet,
            "is_open": s.is_quiet,
            "raw_quiet": s.raw_quiet,
            "reason": s.reason,
            "error": s.error,
            "utilization_percent": s.utilization_percent,
            "rx_per_hour": s.rx_per_hour,
            "current_airtime_ms": s.current_airtime_ms,
            "advert_tier": s.advert_tier,
            "play_max_tier": s.play_max_tier
            or normalize_tier(getattr(self.settings, "play_max_tier", "normal")),
            "adaptive_enabled": s.adaptive_enabled,
            "arl_required": True,
            "adverts_per_min_ewma": s.adverts_per_min_ewma,
            "packets_per_min_ewma": s.packets_per_min_ewma,
            "sampled_at": s.sampled_at,
        }
