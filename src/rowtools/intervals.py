"""Small Intervals.icu client using your own API key.

The key is on your Intervals.icu settings page. The CLI reads it from the
environment (INTERVALS_API_KEY, INTERVALS_ATHLETE_ID). It is never logged.
"""
from __future__ import annotations
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)
MAX_FIT_DOWNLOAD_BYTES = 50 * 1024 * 1024
INTERVALS_BASE = "https://intervals.icu/api/v1"
ACTIVITY_ID_RE = re.compile(r"^[iI]?\d+$")


class IntervalsActivityIdError(ValueError):
    """Invalid activity ID supplied by the client."""


class IntervalsHrPreviewError(RuntimeError):
    """Expected HR preview failure that can be shown to the user."""

    def __init__(self, message: str, *, status_code: int = 422, strava_activity: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.strava_activity = strava_activity


def _validate_activity_id(activity_id: str) -> str:
    value = str(activity_id or "").strip()
    if not ACTIVITY_ID_RE.fullmatch(value):
        raise IntervalsActivityIdError("Invalid Intervals.icu activity_id")
    if value.startswith("I"):
        return "i" + value[1:]
    return value


def _looks_like_fit(content: bytes) -> bool:
    return len(content) >= 12 and content[8:12] == b".FIT"


class IntervalsClient:
    def __init__(self, athlete_id: str, api_key: str, use_oauth: bool = False):
        self.athlete_id = athlete_id.strip()
        self.api_key = api_key.strip()
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        if use_oauth:
            self._session.headers.update({"Authorization": f"Bearer {self.api_key}"})
        else:
            self._session.auth = ("API_KEY", self.api_key)

    def _url(self, path: str) -> str:
        return f"{INTERVALS_BASE}/{path}"

    def validate(self) -> bool:
        try:
            resp = self._session.get(self._url(f"athlete/{self.athlete_id}"), timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.warning("intervals validate failed: %s", e)
            return False

    def get_activities_on_date(self, session_date: date) -> List[Dict[str, Any]]:
        from datetime import timedelta
        oldest_str = session_date.isoformat()
        newest_str = (session_date + timedelta(days=1)).isoformat()
        return self.get_activities(oldest_str, newest_str)

    def get_activities(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """List activities in the requested local-date range."""
        oldest_str = str(start_date)
        newest_str = str(end_date)
        url = self._url(f"athlete/{self.athlete_id}/activities")
        logger.info("get_activities: GET %s oldest=%s newest=%s", url, oldest_str, newest_str)
        resp = self._session.get(url, params={"oldest": oldest_str, "newest": newest_str}, timeout=20)
        if not resp.ok:
            logger.warning("get_activities: %s %s, %s", resp.status_code, resp.reason, resp.text[:200])
        resp.raise_for_status()
        # Filter to only activities on the requested date (API range is [oldest, newest))
        activities = resp.json()
        return [
            self._annotate_activity(a) for a in activities
            if oldest_str <= str(a.get("start_date_local", ""))[:10] < newest_str
        ]

    @classmethod
    def _annotate_activity(cls, activity: Dict[str, Any]) -> Dict[str, Any]:
        annotated = cls._annotate_hr_availability(activity)
        power_value = next(
            (annotated.get(field) for field in (
                "icu_average_power", "average_power", "avg_power", "max_power",
                "icu_average_watts", "watts", "power", "icu_max_watts", "max_watts", "weighted_average_watts",
            ) if annotated.get(field) is not None),
            None,
        )
        annotated["has_power"] = power_value is not None
        annotated["duration_s"] = annotated.get("elapsed_time") or annotated.get("moving_time") or 0
        annotated["device_source"] = next(
            (str(annotated.get(field)) for field in ("device_name", "source", "icu_source")
             if annotated.get(field)),
            "Unknown",
        )
        return annotated

    @staticmethod
    def _annotate_hr_availability(activity: Dict[str, Any]) -> Dict[str, Any]:
        """Annotate activities with whether Intervals.icu can expose HR data."""
        annotated = dict(activity)
        source_values = [
            annotated.get("source"),
            annotated.get("device_name"),
            annotated.get("external_id"),
            annotated.get("icu_source"),
        ]
        is_strava = any(
            "strava" in str(value).lower()
            for value in source_values
            if value is not None
        )
        has_hr_field = any(
            annotated.get(field) is not None
            for field in (
                "icu_average_heartrate",
                "icu_average_hr",
                "icu_average_heart_rate",
                "average_heartrate",
                "average_heart_rate",
                "average_hr",
                "icu_max_hr",
                "icu_max_heartrate",
                "max_heartrate",
                "max_heart_rate",
            )
        )

        if is_strava:
            annotated["has_hr_data"] = False
            annotated["hr_unavailable_reason"] = "strava_synced"
        elif not has_hr_field:
            annotated["has_hr_data"] = False
            annotated["hr_unavailable_reason"] = "no_hr_recorded"
        else:
            annotated["has_hr_data"] = True
            annotated["hr_unavailable_reason"] = None
        annotated["icu_average_hr"] = next(
            (
                annotated.get(field)
                for field in (
                    "icu_average_hr",
                    "icu_average_heartrate",
                    "icu_average_heart_rate",
                    "average_hr",
                    "average_heartrate",
                    "average_heart_rate",
                )
                if annotated.get(field) is not None
            ),
            None,
        )
        annotated["max_heartrate"] = next(
            (
                annotated.get(field)
                for field in ("max_heartrate", "max_heart_rate")
                if annotated.get(field) is not None
            ),
            None,
        )
        return annotated

    def upload_fit(self, fit_path: str, name: Optional[str] = None) -> Dict[str, Any]:
        fit_file = Path(fit_path)
        if not fit_file.exists():
            raise FileNotFoundError(f"FIT file not found: {fit_path}")
        with open(fit_file, "rb") as f:
            resp = self._session.post(
                self._url(f"athlete/{self.athlete_id}/activities"),
                files={"file": (fit_file.name, f, "application/octet-stream")},
                data={"name": name or fit_file.stem},
                timeout=60,
            )
        if resp.status_code == 409:
            raise RuntimeError("Activity already exists on intervals.icu for this athlete on this date.")
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _is_strava_error(resp) -> bool:
        """Return True if the response is a Strava-access 422 from Intervals.icu."""
        if resp.status_code != 422:
            return False
        try:
            return 'strava' in resp.json().get('error', '').lower()
        except Exception:
            return 'strava' in resp.text.lower()

    def download_activity_fit(self, activity_id: str, out_path: str, require_hr: bool = True) -> str:
        activity_id = _validate_activity_id(activity_id)
        # Try file download endpoints first (works for Garmin/native FIT sources)
        for ep in [f"activity/{activity_id}/fit-file", f"activity/{activity_id}/file"]:
            url = self._url(ep)
            logger.info("download_activity_fit: trying GET %s", url)
            resp = self._session.get(url, timeout=60, headers={"Accept": "*/*"})
            if resp.ok and len(resp.content) > 0:
                out = Path(out_path)
                out.parent.mkdir(parents=True, exist_ok=True)
                content = resp.content
                if len(content) > MAX_FIT_DOWNLOAD_BYTES:
                    raise RuntimeError("Intervals.icu file download exceeded size limit")
                if content[:2] == b'\x1f\x8b':  # gzip
                    import gzip
                    import io
                    with gzip.GzipFile(fileobj=io.BytesIO(content)) as gz:
                        content = gz.read(MAX_FIT_DOWNLOAD_BYTES + 1)
                    if len(content) > MAX_FIT_DOWNLOAD_BYTES:
                        raise RuntimeError("Intervals.icu file download exceeded size limit")
                if not _looks_like_fit(content):
                    logger.warning(
                        "download_activity_fit: %s returned non-FIT content_type=%s bytes=%d; trying fallback",
                        ep,
                        resp.headers.get("content-type", ""),
                        len(content),
                    )
                    continue
                out.write_bytes(content)
                logger.info("download_activity_fit: saved %d bytes via %s", len(content), ep)
                return str(out)
            logger.warning("download_activity_fit: %s → %s %s", ep, resp.status_code, resp.reason)
            if self._is_strava_error(resp):
                raise IntervalsHrPreviewError(
                    "This activity was synced from Strava - HR data cannot be accessed "
                    "via the Intervals.icu API. Select a Garmin or native activity instead.",
                    strava_activity=True,
                )

        # Fallback: build FIT from streams (works for non-Strava activities)
        logger.info("download_activity_fit: file endpoints failed, trying streams for %s", activity_id)
        return self._build_fit_from_streams(activity_id, out_path, require_hr=require_hr)

    def _build_fit_from_streams(self, activity_id: str, out_path: str, require_hr: bool = True) -> str:
        """Download activity streams and build a FIT file from the raw data."""
        activity_id = _validate_activity_id(activity_id)
        # Get activity metadata for start time
        meta_url = self._url(f"activity/{activity_id}")
        meta_resp = self._session.get(meta_url, timeout=20)
        meta_resp.raise_for_status()
        meta = meta_resp.json()

        # Get streams (power, hr, cadence, etc.)
        # Intervals.icu streams endpoint: /api/v1/activity/{id}/streams
        streams_url = self._url(f"activity/{activity_id}/streams")
        params = {"types": "time,watts,heartrate,cadence,distance"}
        logger.info("_build_fit_from_streams: GET %s params=%s", streams_url, params)
        streams_resp = self._session.get(streams_url, params=params, timeout=30)
        if not streams_resp.ok:
            logger.warning("_build_fit_from_streams: streams %s, %s",
                          streams_resp.status_code, streams_resp.text[:300])
            if self._is_strava_error(streams_resp):
                raise IntervalsHrPreviewError(
                    "This activity was synced from Strava - HR data cannot be accessed "
                    "via the Intervals.icu API. Select a Garmin or native activity instead.",
                    strava_activity=True,
                )
        streams_resp.raise_for_status()
        streams = streams_resp.json()
        logger.info("_build_fit_from_streams: response keys=%s type=%s",
                     list(streams.keys()) if isinstance(streams, dict) else type(streams).__name__,
                     type(streams).__name__)

        # Parse start time
        from datetime import datetime as _dt, timezone as _tz
        start_str = meta.get("start_date_local") or meta.get("start_date", "")
        try:
            start_dt = _dt.fromisoformat(start_str.replace("Z", "+00:00"))
        except Exception:
            start_dt = _dt.now(_tz.utc)

        # Extract stream arrays. Intervals.icu can return either keyed objects
        # or a list of typed stream objects depending on source/API version.
        def _get_stream(*keys):
            val = None
            if isinstance(streams, dict):
                for key in keys:
                    if key in streams:
                        val = streams.get(key)
                        break
            elif isinstance(streams, list):
                for item in streams:
                    if not isinstance(item, dict):
                        continue
                    stream_type = item.get("type") or item.get("name") or item.get("key")
                    if stream_type in keys:
                        val = item
                        break
            if val is None:
                return []
            if isinstance(val, dict):
                return val.get("data") or val.get("values") or []
            if isinstance(val, list):
                return val
            return []

        time_arr = _get_stream("time", "timer_time", "elapsed_time")
        watts_arr = _get_stream("watts", "power")
        hr_arr = _get_stream("heartrate", "heart_rate", "hr")
        cadence_arr = _get_stream("cadence")
        dist_arr = _get_stream("distance")

        if not time_arr:
            logger.warning("_build_fit_from_streams: no time stream. Available: %s",
                          list(streams.keys()) if isinstance(streams, dict) else str(streams)[:200])
            raise IntervalsHrPreviewError("Intervals.icu did not return stream data for this activity")
        if require_hr and not any(v is not None for v in hr_arr):
            logger.warning("_build_fit_from_streams: no HR stream. Available: %s",
                          list(streams.keys()) if isinstance(streams, dict) else str(streams)[:200])
            raise IntervalsHrPreviewError("No heart rate samples were found in this activity")

        n = len(time_arr)
        logger.info("_build_fit_from_streams: %d samples, watts=%d, hr=%d, cadence=%d",
                     n, len(watts_arr), len(hr_arr), len(cadence_arr))

        # Build FIT file using fit_tool
        from fit_tool.fit_file_builder import FitFileBuilder
        from fit_tool.profile.messages.file_id_message import FileIdMessage
        from fit_tool.profile.messages.record_message import RecordMessage
        from fit_tool.profile import profile_type as PT

        builder = FitFileBuilder()

        # File ID
        file_id = FileIdMessage()
        file_id.type = PT.FileType.ACTIVITY
        file_id.manufacturer = PT.Manufacturer.DEVELOPMENT.value
        file_id.product = 1
        file_id.serial_number = 12345
        start_ms = int(start_dt.astimezone(_tz.utc).timestamp() * 1000)
        file_id.time_created = start_ms
        builder.add(file_id)

        # Records
        for i in range(n):
            rec = RecordMessage()
            rec.timestamp = int(start_ms + round(float(time_arr[i]) * 1000))
            if i < len(watts_arr) and watts_arr[i] is not None:
                rec.power = int(round(watts_arr[i]))
            if i < len(hr_arr) and hr_arr[i] is not None:
                rec.heart_rate = int(round(hr_arr[i]))
            if i < len(cadence_arr) and cadence_arr[i] is not None:
                rec.cadence = int(round(cadence_arr[i]))
            if i < len(dist_arr) and dist_arr[i] is not None:
                rec.distance = int(round(dist_arr[i] * 100))  # FIT uses centimeters
            builder.add(rec)

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fit_file = builder.build()
        out.write_bytes(fit_file.to_bytes())
        logger.info("_build_fit_from_streams: built FIT with %d records → %s", n, out)
        return str(out)

    def delete_activity(self, activity_id: str) -> bool:
        """Delete an activity from intervals.icu. Returns True on success."""
        activity_id = _validate_activity_id(activity_id)
        resp = self._session.delete(
            self._url(f"activity/{activity_id}"),
            timeout=20,
        )
        return resp.status_code in (200, 204)

    @staticmethod
    def format_activity_label(activity: Dict[str, Any]) -> str:
        start = activity.get("start_date_local", "")
        try:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            time_str = dt.strftime("%H:%M")
        except Exception:
            time_str = start[:16] if start else "?"
        name = activity.get("name", "Activity")
        sport = activity.get("type", "")
        moving = activity.get("moving_time", 0)
        duration = f"{moving // 3600}h{(moving % 3600) // 60:02d}m" if moving else ""
        parts = [time_str, name]
        if sport: parts.append(sport)
        if duration: parts.append(duration)
        return "  |  ".join(parts)
