"""PDOK-, OVapi- en NDOV Loket-client (haltebestand CHB)."""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections.abc import Callable
from datetime import date
from itertools import chain
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSSLError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import chb, const
from .const import (
    API_BACKOFF_BASE,
    API_BACKOFF_MAX,
    BATCH_CHUNK,
    CHB_INDEX_MAX_AGE,
    DOMAIN,
    INSECURE_RETRY_AFTER,
    MAX_PARALLEL_REQUESTS,
    OVAPI_BASE,
    PDOK_URL,
    PREVIEW_MAX_STOPS,
    RATE_LIMIT_DEFAULT_WAIT,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from .parser import (
    build_view,
    clean_town,
    describe_tpc,
    distance_m,
    line_options,
    main_destination,
    merged_lines,
    merged_summary,
    parse_passes,
)

_LOGGER = logging.getLogger(__name__)

Progress = Callable[[float], None]
HEADERS = {"User-Agent": USER_AGENT}  # OVapi asks for a User-Agent


class OvapiBlocked(Exception):
    """Automatic call skipped because the API is paused (circuit breaker open)."""


class AddressNotFound(ValueError):
    """PDOK does not know the address."""


def _content_length(r: Any) -> int:
    n = getattr(r, "content_length", None)
    if n is None:
        try:
            n = int(r.headers.get("Content-Length", 0))
        except (AttributeError, TypeError, ValueError):
            n = 0
    return n or 0


def scaled(cb: Progress | None, lo: float, hi: float) -> Progress | None:
    """Map 0..1 onto the sub-range lo..hi of the total progress."""
    if cb is None:
        return None
    return lambda f: cb(lo + (hi - lo) * min(max(f, 0.0), 1.0))


def get_api(hass: HomeAssistant) -> DutchBusApi:
    """One shared client (and in-memory stop cache) per Home Assistant."""
    store = hass.data.setdefault(DOMAIN, {})
    if "api" not in store:
        store["api"] = DutchBusApi(hass)
    return store["api"]


class DutchBusApi:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.session = async_get_clientsession(hass)  # PDOK / NDOV Loket
        self._ovapi_session = async_get_clientsession(hass, verify_ssl=False)
        self.cache_dir = Path(hass.config.path(".storage", DOMAIN))
        self.chb_gz = self.cache_dir / "chb.xml.gz"  # temporary: only while unpacking
        self.psa_gz = self.cache_dir / "chb-psa.xml.gz"
        self.index_path = self.cache_dir / "chb_index.json"  # kept (.storage survives updates)
        self._source_date = ""
        self._insecure_since: float | None = None  # after an SSL error: no verification until INSECURE_RETRY_AFTER
        self._pending: list[tuple[list[str], asyncio.Future, bool]] = []
        self._flush_task: asyncio.Task | None = None
        # health, circuit breaker and pacing of automatic calls (manual calls and the config flow bypass them)
        self.spacing = 0.0
        self.max_errors = 10
        self.consecutive_errors = 0
        self._blocked_until = 0.0
        self._was_outage = False
        self._pace_lock = asyncio.Lock()
        self._last_call = 0.0
        # called with True/False when the outage state changes (used for the repair issue)
        self.on_outage_change: Callable[[bool], None] | None = None

    # ------------------------------------------------------------------ health / circuit breaker
    def configure(self, *, spacing: float, max_errors: int) -> None:
        """Apply the error protection settings and start with a clean state (after a reload)."""
        self.spacing = spacing
        self.max_errors = max_errors
        self.consecutive_errors = 0
        self._blocked_until = 0.0
        self._was_outage = False
        self._last_call = 0.0

    @property
    def outage(self) -> bool:
        """True while automatic calls are paused because of repeated API errors or rate limiting."""
        return time.monotonic() < self._blocked_until

    @property
    def blocked(self) -> bool:
        return self.outage

    @property
    def blocked_for(self) -> float:
        return max(self._blocked_until - time.monotonic(), 0.0)

    def _notify(self) -> None:
        outage = self.outage
        if outage != self._was_outage:
            self._was_outage = outage
            if self.on_outage_change:
                self.on_outage_change(outage)

    def _record_success(self) -> None:
        self.consecutive_errors = 0
        self._blocked_until = 0.0
        self._notify()

    def _record_failure(self, retry_after: float | None = None) -> None:
        self.consecutive_errors += 1
        wait = 0.0
        if retry_after is not None:
            wait = min(retry_after, API_BACKOFF_MAX)
        elif self.consecutive_errors >= self.max_errors:
            wait = min(API_BACKOFF_BASE * 2 ** (self.consecutive_errors - self.max_errors), API_BACKOFF_MAX)
        if wait:
            self._blocked_until = max(self._blocked_until, time.monotonic() + wait)
            _LOGGER.warning("OVapi paused for %d s after %d consecutive errors", wait, self.consecutive_errors)
        self._notify()

    @staticmethod
    def _retry_after(err: BaseException) -> float | None:
        """Seconds to wait after an HTTP 429 (``Retry-After`` header, otherwise a default); None for other errors."""
        if isinstance(err, ClientResponseError) and err.status == 429:
            value = err.headers.get("Retry-After", "") if err.headers else ""
            return float(value) if str(value).isdigit() else float(RATE_LIMIT_DEFAULT_WAIT)
        return None

    async def _pace(self) -> None:
        """Spread automatic calls: at least ``spacing`` seconds between them (FIFO), even with equal schedules."""
        if self.spacing <= 0:
            return
        async with self._pace_lock:
            wait = self._last_call + self.spacing - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    # ------------------------------------------------------------------ PDOK
    async def async_geocode(self, address: str, place: bool = False) -> tuple[float, float, str]:
        """Address or (``place=True``) place name to (lat, lon, display name); a place gives the village/town centre."""
        params: dict[str, str | int] = {"q": address, "rows": 10, "fl": "weergavenaam,centroide_ll,type"}
        if place:
            params["fq"] = "type:woonplaats"
        async with self.session.get(
            PDOK_URL, params=params, headers=HEADERS, timeout=ClientTimeout(total=25)
        ) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
        docs = data.get("response", {}).get("docs", [])
        if not docs:
            raise AddressNotFound(address)
        doc = next((x for x in docs if x.get("type") == ("woonplaats" if place else "adres")), docs[0])
        point = doc.get("centroide_ll", "")
        if not point.startswith("POINT("):
            raise AddressNotFound(address)
        lon, lat = map(float, point[6:-1].split())
        return lat, lon, doc.get("weergavenaam", address)

    async def async_locate(self, text: str) -> tuple[float, float, str, str]:
        """Address lookup: place name, street + place or address -> (lat, lon, display name, type).

        ``type`` is ``woonplaats`` (town centre), ``weg`` (point on the street), ``adres`` or ``postcode``.
        """
        params: dict[str, str | int] = {
            "q": text,
            "rows": 5,
            "fl": "weergavenaam,centroide_ll,type",
            "fq": "type:(woonplaats OR weg OR adres OR postcode)",
        }
        async with self.session.get(
            PDOK_URL, params=params, headers=HEADERS, timeout=ClientTimeout(total=25)
        ) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
        for doc in data.get("response", {}).get("docs", []):
            point = doc.get("centroide_ll", "")
            if point.startswith("POINT("):
                lon, lat = map(float, point[6:-1].split())
                return lat, lon, doc.get("weergavenaam", text), doc.get("type", "")
        raise AddressNotFound(text)

    # ------------------------------------------------------------------ OVapi
    async def _ovapi_request(self, session: Any, path: str, timeout: int) -> Any:
        async with session.get(
            f"{OVAPI_BASE}/{path}", headers=HEADERS, timeout=ClientTimeout(total=timeout)
        ) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
        if not isinstance(data, dict):
            raise ValueError("Unexpected response from OVapi")
        return data

    async def _ovapi_get_once(self, path: str, timeout: int) -> Any:
        """First with certificate verification; only on an SSL error (known issue of v0.ovapi.nl) without."""
        if self._insecure_since is not None and time.monotonic() - self._insecure_since < INSECURE_RETRY_AFTER:
            return await self._ovapi_request(self._ovapi_session, path, timeout)
        try:
            data = await self._ovapi_request(self.session, path, timeout)
        except (ClientSSLError, ssl.SSLError, ssl.CertificateError) as err:
            _LOGGER.info("OVapi certificate not verified (%s); verification is skipped for this host", err)
            self._insecure_since = time.monotonic()
            return await self._ovapi_request(self._ovapi_session, path, timeout)
        self._insecure_since = None
        return data

    @staticmethod
    def _transient(err: BaseException) -> bool:
        if isinstance(err, ClientResponseError):
            return err.status >= 500  # 429 is not retried: the circuit breaker handles rate limiting
        return isinstance(err, (ClientError, TimeoutError, asyncio.TimeoutError))

    async def _ovapi_get(self, path: str, timeout: int = REQUEST_TIMEOUT, retries: int = 0) -> Any:
        """GET with (optional) retries on transient errors: wait times from ``const.RETRY_DELAYS``."""
        attempt = 0
        while True:
            try:
                return await self._ovapi_get_once(path, timeout)
            except Exception as err:
                if attempt >= retries or not self._transient(err):
                    raise
                delay = const.RETRY_DELAYS[min(attempt, len(const.RETRY_DELAYS) - 1)]
                _LOGGER.debug("OVapi request failed (%s), retrying in %s s", err, delay)
                attempt += 1
                await asyncio.sleep(delay)

    async def async_fetch_tpcs(self, codes: list[str], timeout: int = REQUEST_TIMEOUT, retries: int = 0) -> dict[str, Any]:
        """GET /tpc/<c1>,<c2>,… (recommended by OVapi: one request for several codes)."""
        return await self._ovapi_get(f"tpc/{','.join(codes)}", timeout, retries)

    async def async_fetch_tpc(self, code: str, timeout: int = REQUEST_TIMEOUT, retries: int = 0) -> dict[str, Any]:
        return await self.async_fetch_tpcs([code], timeout, retries)

    async def async_fetch_many(self, codes: list[str], retries: int = 0) -> dict[str, Any]:
        """Fetch each code separately (concurrently); the response or the exception per code."""
        limit = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)

        async def one(code: str) -> dict[str, Any]:
            async with limit:
                return await self.async_fetch_tpc(code, retries=retries)

        results = await asyncio.gather(*(one(c) for c in codes), return_exceptions=True)
        return dict(zip(codes, results, strict=True))

    async def async_fetch_group(self, codes: list[str], *, auto: bool = False) -> dict[str, Any]:
        """Codes of one stop. When several stops refresh at once, all codes go along in one request.

        ``auto=True`` (schedule, zone) honours the circuit breaker and the pacing; manual calls never wait.
        """
        future: asyncio.Future = self.hass.loop.create_future()
        self._pending.append((list(codes), future, auto))
        if self._flush_task is None:
            self._flush_task = self.hass.async_create_task(self._flush_pending())
        return await future

    async def _flush_pending(self) -> None:
        await asyncio.sleep(0)  # wait until all stops that refresh at the same moment have registered
        pending, self._pending, self._flush_task = self._pending, [], None
        try:
            await self._flush(pending)
        except BaseException as err:  # so no stop waits forever
            for _, future, _ in pending:
                if not future.done():
                    future.set_exception(err if isinstance(err, Exception) else RuntimeError("cancelled"))
            if not isinstance(err, Exception):
                raise

    async def _flush(self, pending: list[tuple[list[str], asyncio.Future, bool]]) -> None:
        # Only a batch of purely automatic refreshes is paced and blocked; one manual refresh lets it all through
        automatic = all(auto for _, _, auto in pending)
        if automatic:
            if self.blocked:
                self._fail_all(pending, OvapiBlocked(f"OVapi paused for another {self.blocked_for:.0f} s"))
                return
            await self._pace()
            if self.blocked:  # the state may have changed while waiting
                self._fail_all(pending, OvapiBlocked(f"OVapi paused for another {self.blocked_for:.0f} s"))
                return
        all_codes = list(dict.fromkeys(c for codes, _, _ in pending for c in codes))
        results: dict[str, Any] = {}
        for i in range(0, len(all_codes), BATCH_CHUNK):
            chunk = all_codes[i : i + BATCH_CHUNK]
            try:
                payload = await self.async_fetch_tpcs(chunk, retries=2)
                results.update(dict.fromkeys(chunk, payload))
                self._record_success()
            except Exception as err:  # noqa: BLE001
                if self._retry_after(err) is not None:  # rate limited: do not hammer with single requests
                    self._record_failure(self._retry_after(err))
                    results.update(dict.fromkeys(chunk, err))
                    continue
                if len(chunk) == 1:
                    results[chunk[0]] = err
                    self._record_failure()
                    continue
                _LOGGER.debug("Combined request failed (%s), retrying per code", err)
                singles = await self.async_fetch_many(chunk, retries=1)
                results.update(singles)
                if all(isinstance(v, BaseException) for v in singles.values()):
                    self._record_failure(next((self._retry_after(v) for v in singles.values() if self._retry_after(v)), None))
                else:
                    self._record_success()
        for codes, future, _ in pending:
            if not future.done():
                future.set_result({c: results[c] for c in codes})

    @staticmethod
    def _fail_all(pending: list[tuple[list[str], asyncio.Future, bool]], err: Exception) -> None:
        for _, future, _ in pending:
            if not future.done():
                future.set_exception(err)

    async def async_validate_tpcs(
        self, codes: list[str], near: dict[str, tuple[float, float]] | None = None
    ) -> dict[str, dict[str, Any] | None]:
        """Check codes live against OVapi (bundled per chunk). ``near``: expected (lat, lon) per code."""
        results: dict[str, Any] = {}
        for i in range(0, len(codes), BATCH_CHUNK):
            chunk = codes[i : i + BATCH_CHUNK]
            if len(chunk) > 1:
                try:
                    payload = await self.async_fetch_tpcs(chunk)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Combined check failed (%s), checking per code", err)
                else:
                    results.update(dict.fromkeys(chunk, payload))
                    continue
            results.update(await self.async_fetch_many(chunk))
        out: dict[str, dict[str, Any] | None] = {}
        for code, res in results.items():
            if isinstance(res, BaseException):
                _LOGGER.debug("Checking TPC %s failed: %s", code, res)
                out[code] = None
                continue
            info = describe_tpc(code, res)
            expected = (near or {}).get(code)
            if (
                info
                and expected
                and info["lat"] is not None
                and distance_m(expected[0], expected[1], info["lat"], info["lon"]) > 500
            ):
                info = None
            out[code] = info
        return out

    # ------------------------------------------------------------------ NDOV Loket-haltebestand (CHB)
    def _index_age(self) -> float | None:
        try:
            return time.time() - self.index_path.stat().st_mtime
        except OSError:
            return None

    def _index_version(self) -> int:
        try:
            return int(json.loads(self.index_path.with_name("chb_meta.json").read_text(encoding="utf-8")).get("version", 1))
        except (OSError, ValueError):
            return 1

    async def async_chb_state(self) -> str:
        """'fresh' (index recent enough), 'stale' (present but old or of an older layout) or 'missing'."""
        age = await self.hass.async_add_executor_job(self._index_age)
        if age is None:
            return "missing"
        if await self.hass.async_add_executor_job(self._index_version) < chb.INDEX_VERSION:
            return "stale"  # oude index kent alleen bussen: opnieuw opbouwen voor tram/metro/veerboot
        return "fresh" if age <= CHB_INDEX_MAX_AGE else "stale"

    async def async_chb_info(self) -> dict[str, Any] | None:
        """Age of the processed NDOV stop file: ``{source_date, built, quays, age_days, state}``."""
        def read() -> dict[str, Any] | None:
            try:
                mtime = self.index_path.stat().st_mtime
            except OSError:
                return None
            try:
                meta = json.loads(self.index_path.with_name("chb_meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            age = max(int((time.time() - mtime) // 86400), 0)
            # age of the source file itself (date stamp in the file name), otherwise of the processing
            stamp = meta.get("source_date") or meta.get("built") or ""
            try:
                file_age = max((date.today() - date.fromisoformat(stamp)).days, 0)
            except ValueError:
                file_age = age
            return {
                "file_age_days": file_age,
                "source_date": meta.get("source_date") or "",
                "built": meta.get("built") or "",
                "quays": meta.get("quays"),
                "age_days": age,
                "state": "fresh" if age * 86400 <= CHB_INDEX_MAX_AGE else "stale",
            }

        return await self.hass.async_add_executor_job(read)

    async def _async_chb_urls(self) -> tuple[str, str | None, str]:
        last: Exception | None = None
        for base in chb.LISTING_URLS:
            try:
                async with self.session.get(base, headers=HEADERS, timeout=ClientTimeout(total=30)) as r:
                    r.raise_for_status()
                    html = await r.text()
                return chb.parse_listing(html, base)
            except Exception as err:  # noqa: BLE001
                last = err
        raise last or ValueError("NDOV Loket not reachable")

    async def _async_download(self, url: str, dest: Path, progress: Progress | None) -> None:
        job = self.hass.async_add_executor_job
        await job(lambda: dest.parent.mkdir(parents=True, exist_ok=True))
        tmp = dest.with_suffix(".part")
        headers = {**HEADERS, "Accept-Encoding": "identity"}  # Content-Length = echte bytes
        try:
            async with self.session.get(url, headers=headers, timeout=ClientTimeout(total=1800, sock_read=120)) as r:
                r.raise_for_status()
                total, done = _content_length(r), 0
                fh = await job(tmp.open, "wb")
                try:
                    async for chunk in r.content.iter_chunked(1 << 18):
                        await job(fh.write, chunk)
                        done += len(chunk)
                        if progress and total:
                            progress(min(done / total, 1.0))
                finally:
                    await job(fh.close)
            await job(tmp.replace, dest)
        except Exception:
            await job(lambda: tmp.unlink(missing_ok=True))
            raise
        if progress:
            progress(1.0)

    async def async_download_chb(self, progress: Progress | None = None) -> None:
        """Download ExportCHB (~13 MB) and the stop lookup table (~1 MB) from NDOV Loket; progress 0 → 1."""
        chb_url, psa_url, self._source_date = await self._async_chb_urls()
        weight = 0.93 if psa_url else 1.0
        await self._async_download(chb_url, self.chb_gz, scaled(progress, 0.0, weight))
        if psa_url:
            try:
                await self._async_download(psa_url, self.psa_gz, scaled(progress, weight, 1.0))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Stop lookup table not downloaded (%s); only the quay number is tried as TPC", err)
        if progress:
            progress(1.0)

    async def async_build_chb_index(self, progress: Progress | None = None) -> int:
        """Unpack the downloads (~300 MB, streamed) and store a compact index; progress 0 → 1."""
        loop = self.hass.loop

        def report(fraction: float) -> None:  # draait in een thread
            if progress:
                loop.call_soon_threadsafe(progress, fraction)

        def build() -> int:
            psa = self.psa_gz if self.psa_gz.exists() else None
            return chb.build_index(self.chb_gz, psa, self.index_path, report, self._source_date)

        try:
            return await self.hass.async_add_executor_job(build)
        finally:
            await self.hass.async_add_executor_job(self._remove_download_files)

    def _remove_download_files(self) -> None:
        for path in (self.chb_gz, self.psa_gz):
            path.unlink(missing_ok=True)

    async def async_quays_near(
        self, lat: float, lon: float, radius: float, modes: tuple[str, ...] | list[str] = ("bus",)
    ) -> list[dict[str, Any]]:
        def find() -> list[dict[str, Any]]:
            index = json.loads(self.index_path.read_text(encoding="utf-8"))
            return chb.find_quays(index, lat, lon, radius, modes=modes)

        return await self.hass.async_add_executor_job(find)

    # ------------------------------------------------------------------ candidates for the config flow
    async def async_candidates(
        self,
        lat: float,
        lon: float,
        radius: int,
        tz: Any,
        now: Any,
        progress: Progress | None = None,
        modes: tuple[str, ...] | list[str] = ("bus",),
    ) -> tuple[list[dict[str, Any]], int]:
        """Bus stops (quays = one direction of travel) in the zone, with live next departures.

        Returns ``(candidates, count_without_live_data)``. A quay only counts if OVapi
        knows at least one of its possible TPC codes.
        """
        quays = (await self.async_quays_near(lat, lon, radius, modes))[:PREVIEW_MAX_STOPS * 2]
        if progress:
            progress(0.1)
        codes = list(dict.fromkeys(c for q in quays for c in q["tpc_candidates"]))
        info: dict[str, Any] = {}
        views: dict[str, list[dict[str, Any]]] = {}
        lines_of: dict[str, dict[str, str]] = {}
        chunks = [codes[i : i + 40] for i in range(0, len(codes), 40)]
        for n, chunk in enumerate(chunks, start=1):
            try:
                payload = await self.async_fetch_tpcs(chunk, timeout=20)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Fetching live data failed: %s", err)
                continue
            for c in chunk:
                info[c] = describe_tpc(c, payload)
                if info[c]:
                    deps = parse_passes(payload, c, tz)
                    views[c] = build_view(deps, now)
                    lines_of[c] = line_options(deps)
            if progress:
                progress(0.1 + 0.9 * n / len(chunks))

        out: list[dict[str, Any]] = []
        skipped = 0
        for q in quays:
            valid = [c for c in q["tpc_candidates"] if info.get(c)]
            if not valid:
                skipped += 1
                continue
            view = sorted(chain.from_iterable(views[c] for c in valid), key=lambda d: d["exp"])
            lines: dict[str, str] = {}
            for c in valid:
                lines.update({k: v for k, v in lines_of[c].items() if k not in lines})
            out.append(
                {
                    "tpc": valid[0],
                    "tpc_codes": valid,
                    "quay": q["quay"],
                    "name": q["name"] or info[valid[0]].get("name") or f"Stop {valid[0]}",
                    "town": clean_town(q["town"]) or clean_town(info[valid[0]].get("town")),
                    "side": q["side"],
                    "bearing": q["bearing"],
                    "lat": q["lat"],
                    "lon": q["lon"],
                    "distance_m": q["distance_m"],
                    "preview": merged_summary(view),
                    "departures": merged_lines(view),
                    "destination": main_destination(view),
                    "lines": lines,
                }
            )
        return out, skipped

    async def async_line_options(self, codes: list[str], tz: Any) -> dict[str, str]:
        """Lines currently present at the stop (for the line filter with checkboxes)."""
        results = await self.async_fetch_group(codes)
        departures: list[dict[str, Any]] = []
        for code, res in results.items():
            if not isinstance(res, BaseException):
                departures += parse_passes(res, code, tz)
        return line_options(departures)
