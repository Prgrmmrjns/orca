"""1h and 4h UTC panels from Kraken public history.

Kraken's public OHLC REST call returns at most ~720 bars. Longer history is
read from the downloadable OHLCVT archives (the split full zip, or a quarterly
zip already on disk). The recent tail is stitched from REST. The bar that is
still forming is always dropped.

Raw zips are not committed. A small sample and ``data/crypto/MANIFEST.json``
are. Rebuild with ``python -m crypto data``.
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
import time
import urllib.request
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from crypto.spec import ASSETS, INTERVALS, KRAKEN_REST, PAIR_STEMS, PANEL_START, PAIRS

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "crypto" / "cache"
RAW = ROOT / "data" / "crypto" / "raw"
SAMPLE = ROOT / "data" / "crypto" / "sample"
MANIFEST = ROOT / "data" / "crypto" / "MANIFEST.json"
CLOSES_CSV = ROOT / "research" / "closes.csv"
LOCAL_ZIPS = (ROOT / "data" / "ohlcvt", RAW)

FULL_PARTS = tuple(
    f"https://assets.kraken.com/marketing/institutions/Kraken_OHLCVT_Full.zip.part{i:02d}"
    for i in range(5)
)
QUARTERLY_ZIPS = (
    "https://assets.kraken.com/marketing/institutions/Kraken_OHLCVT_2026Q1.zip",
    "https://assets.kraken.com/marketing/institutions/Kraken_OHLCVT_2026Q2.zip",
    "https://assets.kraken.com/marketing/institutions/Kraken_OHLCVT_2026Q3.zip",
)

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def drop_unfinished(df: pd.DataFrame, minutes: int, now: datetime | None = None) -> pd.DataFrame:
    """Drop the bar that opened at the current interval, and anything later."""
    if df is None or len(df) == 0:
        return df
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    step = int(minutes) * 60
    start = pd.Timestamp((int(now.timestamp()) // step) * step, unit="s", tz="UTC")
    out = df[df.index < start]
    return out.sort_index()


def read_ohlcvt_bytes(raw: bytes) -> pd.DataFrame:
    """Parse a headerless Kraken OHLCVT CSV. Same columns as orca.data._read_ohlcvt_csv."""
    df = pd.read_csv(
        io.BytesIO(raw),
        header=None,
        names=["timestamp", "open", "high", "low", "close", "volume", "trades"],
    )
    if len(df) == 0:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    unit = "ms" if float(df["timestamp"].iloc[0]) > 1e12 else "s"
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit=unit, utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df[OHLCV_COLUMNS].astype(float)


def resample_ohlcv(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Left-labelled, left-closed resample. Empty buckets are dropped."""
    rule = f"{int(minutes)}min"
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["close"])


def to_grid(df: pd.DataFrame, minutes: int, now: datetime | None = None) -> pd.DataFrame:
    """Regular grid. Missing bars keep the last traded close (causal) and zero volume."""
    df = drop_unfinished(df, minutes, now=now)
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    grid = pd.date_range(df.index[0], df.index[-1], freq=f"{int(minutes)}min", tz="UTC")
    out = df.reindex(grid)
    out["close"] = out["close"].ffill()
    for col in ("open", "high", "low"):
        out[col] = out[col].fillna(out["close"])
    out["volume"] = out["volume"].fillna(0.0)
    out = out.dropna(subset=["close"])
    out = out[(out["close"] > 0) & (out["open"] > 0)]
    return out


def align_assets(
    frames: dict[str, pd.DataFrame],
    *,
    start: str = PANEL_START,
) -> dict[str, pd.DataFrame]:
    """Inner-join assets from the later of ``start`` and each series' first print."""
    if not frames:
        return {}
    start_ts = pd.Timestamp(start, tz="UTC")
    firsts = []
    trimmed: dict[str, pd.DataFrame] = {}
    for asset, df in frames.items():
        df = df[df.index >= start_ts]
        if len(df) == 0:
            continue
        trimmed[asset] = df
        firsts.append(df.index[0])
    if not trimmed:
        return {}
    begin = max(firsts)
    end = min(df.index[-1] for df in trimmed.values())
    if begin > end:
        return {}
    return {a: df.loc[(df.index >= begin) & (df.index <= end)] for a, df in trimmed.items()}


def _request(url: str, headers: dict | None = None, timeout: int = 120) -> bytes:
    hdrs = {"User-Agent": "orca-paper-panel"}
    if headers:
        hdrs.update(headers)
    last: Exception | None = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read()
        except Exception as exc:  # noqa: BLE001 — public fetch, retry then raise
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def _head_length(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "orca-paper-panel"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return int(res.headers["Content-Length"])


class _MultiPart:
    """Random-access reader over HTTP range requests on zip parts."""

    def __init__(self, urls: tuple[str, ...] | list[str]):
        self.urls = list(urls)
        self.sizes = [_head_length(u) for u in self.urls]
        self.bounds: list[tuple[int, int]] = []
        acc = 0
        for n in self.sizes:
            self.bounds.append((acc, acc + n))
            acc += n
        self.total = acc

    def fetch(self, start: int, length: int) -> bytes:
        if length <= 0:
            return b""
        out = bytearray()
        pos = start
        end = start + length
        for (b0, b1), url in zip(self.bounds, self.urls):
            if pos >= end:
                break
            if end <= b0 or pos >= b1:
                continue
            a = max(pos, b0)
            c = min(end, b1)
            rs, re = a - b0, c - b0 - 1
            chunk = _request(url, {"Range": f"bytes={rs}-{re}"})
            if len(chunk) != (re - rs + 1):
                raise RuntimeError(f"short range read from {url}: {len(chunk)} != {re - rs + 1}")
            out += chunk
            pos = c
        if len(out) != length:
            raise RuntimeError(f"assembled {len(out)} bytes, expected {length}")
        return bytes(out)


def _zip64_cd(reader: _MultiPart) -> tuple[int, int]:
    tail_n = 256 * 1024
    tail = reader.fetch(reader.total - tail_n, tail_n)
    idx = tail.rfind(b"PK\x05\x06")
    if idx < 0:
        raise RuntimeError("EOCD not found in archive tail")
    eocd = tail[idx : idx + 22]
    _disk, _disk_cd, _disk_entries, total_entries, cd_size, cd_offset, _comment = struct.unpack(
        "<HHHHIIH", eocd[4:22]
    )
    if cd_offset == 0xFFFFFFFF or total_entries == 0xFFFF or cd_size == 0xFFFFFFFF:
        loc = tail[idx - 20 : idx]
        if loc[:4] != b"PK\x06\x07":
            raise RuntimeError("ZIP64 locator missing")
        z64_off = struct.unpack("<Q", loc[8:16])[0]
        z64 = reader.fetch(z64_off, 56)
        if z64[:4] != b"PK\x06\x06":
            raise RuntimeError("ZIP64 EOCD missing")
        cd_size = struct.unpack("<Q", z64[40:48])[0]
        cd_offset = struct.unpack("<Q", z64[48:56])[0]
    return int(cd_offset), int(cd_size)


def _parse_cd(cd: bytes) -> list[tuple[str, int, int, int]]:
    """Return (name, method, comp_size, local_offset) for every member."""
    pos = 0
    found: list[tuple[str, int, int, int]] = []
    while pos + 46 <= len(cd):
        if cd[pos : pos + 4] != b"PK\x01\x02":
            break
        method = struct.unpack("<H", cd[pos + 10 : pos + 12])[0]
        comp_size, uncomp_size = struct.unpack("<II", cd[pos + 20 : pos + 28])
        name_len, extra_len, comment_len = struct.unpack("<HHH", cd[pos + 28 : pos + 34])
        local_off = struct.unpack("<I", cd[pos + 42 : pos + 46])[0]
        name = cd[pos + 46 : pos + 46 + name_len].decode("utf-8", "replace")
        extra = cd[pos + 46 + name_len : pos + 46 + name_len + extra_len]
        if comp_size == 0xFFFFFFFF or uncomp_size == 0xFFFFFFFF or local_off == 0xFFFFFFFF:
            ep = 0
            while ep + 4 <= len(extra):
                hid, hsz = struct.unpack("<HH", extra[ep : ep + 4])
                payload = extra[ep + 4 : ep + 4 + hsz]
                ep += 4 + hsz
                if hid != 0x0001:
                    continue
                p = 0
                if uncomp_size == 0xFFFFFFFF:
                    p += 8
                if comp_size == 0xFFFFFFFF:
                    comp_size = struct.unpack("<Q", payload[p : p + 8])[0]
                    p += 8
                if local_off == 0xFFFFFFFF:
                    local_off = struct.unpack("<Q", payload[p : p + 8])[0]
        found.append((name, int(method), int(comp_size), int(local_off)))
        pos += 46 + name_len + extra_len + comment_len
    return found


def _inflate_member(blob: bytes, method: int) -> bytes:
    if method == 0:
        return blob
    if method == 8:
        return zlib.decompress(blob, -15)
    raise RuntimeError(f"unsupported zip method {method}")


def _read_local(reader: _MultiPart, method: int, comp_size: int, local_off: int) -> bytes:
    head = reader.fetch(local_off, 30)
    if head[:4] != b"PK\x03\x04":
        raise RuntimeError("bad local header")
    name_len, extra_len = struct.unpack("<HH", head[26:30])
    data = reader.fetch(local_off + 30 + name_len + extra_len, comp_size)
    return _inflate_member(data, method)


def _wanted(name: str, stems: tuple[str, ...], interval: int) -> bool:
    base = name.rsplit("/", 1)[-1]
    return any(base == f"{stem}_{interval}.csv" for stem in stems)


def extract_pair_interval(
    reader: _MultiPart,
    catalog: list[tuple[str, int, int, int]],
    stems: tuple[str, ...],
    interval: int,
) -> pd.DataFrame | None:
    frames = []
    for name, method, comp_size, local_off in catalog:
        if not _wanted(name, stems, interval):
            continue
        raw = _read_local(reader, method, comp_size, local_off)
        frames.append(read_ohlcvt_bytes(raw))
    if not frames:
        return None
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]


def _frames_from_zipfile(path: Path, stems: tuple[str, ...], interval: int) -> pd.DataFrame | None:
    frames = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if _wanted(name, stems, interval):
                frames.append(read_ohlcvt_bytes(zf.read(name)))
    if not frames:
        return None
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]


def load_local_archives(asset: str, interval: int) -> pd.DataFrame | None:
    stems = PAIR_STEMS[asset]
    frames = []
    for folder in LOCAL_ZIPS:
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.zip")):
            got = _frames_from_zipfile(path, stems, interval)
            if got is not None and len(got):
                frames.append(got)
    if not frames:
        return None
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]


_CATALOG: list[tuple[str, int, int, int]] | None = None
_READER: _MultiPart | None = None


def _full_catalog() -> tuple[_MultiPart, list[tuple[str, int, int, int]]]:
    global _CATALOG, _READER
    if _READER is None or _CATALOG is None:
        _READER = _MultiPart(FULL_PARTS)
        offset, size = _zip64_cd(_READER)
        _CATALOG = _parse_cd(_READER.fetch(offset, size))
    return _READER, _CATALOG


def fetch_rest_ohlc(asset: str, minutes: int, timeout: int = 30) -> pd.DataFrame:
    """Latest public OHLC. Kraken caps this at ~720 bars and includes the open bar."""
    pair = KRAKEN_REST[asset]
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={int(minutes)}"
    raw = json.loads(_request(url, timeout=timeout))
    if raw.get("error"):
        raise RuntimeError(f"Kraken OHLC {pair}: {raw['error']}")
    result = raw["result"]
    rows = next(v for k, v in result.items() if k != "last")
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "vwap", "volume", "trades"])
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="s", utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df[OHLCV_COLUMNS].astype(float)


def _cache_path(asset: str, minutes: int, kind: str) -> Path:
    return CACHE / f"{asset}_{minutes}_{kind}.csv"


def _read_cache(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df.sort_index()


def _write_cache(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out.index.name = "timestamp"
    out.to_csv(path)


def load_asset_interval(
    asset: str,
    minutes: int,
    *,
    now: datetime | None = None,
    use_network: bool = True,
) -> pd.DataFrame:
    """Archive history (cached) plus the public REST tail, unfinished bar removed."""
    archive_path = _cache_path(asset, minutes, "archive")
    archive = _read_cache(archive_path)
    if archive is None:
        archive = load_local_archives(asset, minutes)
        if archive is None and use_network:
            reader, catalog = _full_catalog()
            archive = extract_pair_interval(reader, catalog, PAIR_STEMS[asset], minutes)
        if archive is not None and len(archive):
            _write_cache(archive, archive_path)
    parts = []
    if archive is not None and len(archive):
        parts.append(archive)
    if use_network:
        parts.append(fetch_rest_ohlc(asset, minutes))
        time.sleep(0.4)
    if not parts:
        raise FileNotFoundError(f"no {minutes}m history for {asset}")
    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = to_grid(df, minutes, now=now)
    df = df[df.index >= pd.Timestamp(PANEL_START, tz="UTC")]
    _write_cache(df, _cache_path(asset, minutes, "panel"))
    return df


def build_interval(
    minutes: int,
    *,
    assets: tuple[str, ...] = ASSETS,
    now: datetime | None = None,
    use_network: bool = True,
) -> dict[str, pd.DataFrame]:
    frames = {a: load_asset_interval(a, minutes, now=now, use_network=use_network) for a in assets}
    return align_assets(frames)


def daily_closes_from_panel(frame: pd.DataFrame) -> pd.Series:
    close = frame["close"]
    days = close.index.tz_convert("UTC").floor("D")
    return close.groupby(days).last()


def cross_check_closes(
    panels: dict[str, pd.DataFrame],
    closes_csv: Path | None = None,
) -> dict:
    path = Path(closes_csv) if closes_csv else CLOSES_CSV
    report: dict = {"path": str(path), "present": path.exists(), "pairs": {}}
    if not path.exists():
        return report
    ref = pd.read_csv(path, index_col=0, parse_dates=True)
    if ref.index.tz is None:
        ref.index = ref.index.tz_localize("UTC")
    else:
        ref.index = ref.index.tz_convert("UTC")
    for asset, frame in panels.items():
        if asset not in ref.columns:
            report["pairs"][asset] = {"overlap": 0}
            continue
        got = daily_closes_from_panel(frame)
        other = ref[asset].dropna()
        other.index = other.index.floor("D")
        joined = pd.concat([got.rename("kraken"), other.rename("ref")], axis=1, join="inner").dropna()
        if len(joined) == 0:
            report["pairs"][asset] = {"overlap": 0}
            continue
        pct = (joined["kraken"] / joined["ref"] - 1.0).abs()
        report["pairs"][asset] = {
            "overlap": int(len(joined)),
            "median_abs_pct": float(pct.median()),
            "p95_abs_pct": float(pct.quantile(0.95)),
            "corr": float(joined["kraken"].corr(joined["ref"])),
        }
    return report


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def write_sample(panels: dict[str, dict[str, pd.DataFrame]], bars: int = 42) -> list[str]:
    """Commit a short fixed window (or the first bars) so the schema is in git."""
    SAMPLE.mkdir(parents=True, exist_ok=True)
    written = []
    window_start = pd.Timestamp("2024-01-01", tz="UTC")
    window_end = pd.Timestamp("2024-01-15", tz="UTC")
    for label, frames in panels.items():
        if not frames:
            continue
        cols = {}
        idx = None
        for asset, df in frames.items():
            sl = df.loc[(df.index >= window_start) & (df.index < window_end)]
            if len(sl) == 0:
                sl = df.iloc[:bars]
            idx = sl.index if idx is None else idx.intersection(sl.index)
            cols[asset] = sl["close"]
        if idx is None or len(idx) == 0:
            continue
        out = pd.DataFrame({a: s.reindex(idx) for a, s in cols.items()})
        out.index.name = "timestamp"
        path = SAMPLE / f"{label}_closes_sample.csv"
        out.to_csv(path)
        written.append(str(path.relative_to(ROOT)))
    return written


def write_manifest(panels: dict[str, dict[str, pd.DataFrame]], cross: dict, samples: list[str]) -> dict:
    body: dict = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "archives": list(FULL_PARTS),
            "quarterly_optional": list(QUARTERLY_ZIPS),
            "rest": "https://api.kraken.com/0/public/OHLC",
            "rest_cap_bars": 720,
            "note": "Full history is range-read from the split OHLCVT zip. The open bar is dropped.",
        },
        "panel_start_requested": PANEL_START,
        "intervals": {},
        "cross_check": cross,
        "sample": samples,
    }
    for label, frames in panels.items():
        minutes = INTERVALS[label]
        info = {"joint": {}, "per_asset_from_2018_or_listing": {}}
        for asset, df in frames.items():
            cache = _cache_path(asset, minutes, "panel")
            info["joint"][asset] = {
                "rows": int(len(df)),
                "start": df.index[0].isoformat() if len(df) else None,
                "end": df.index[-1].isoformat() if len(df) else None,
            }
            cached = _read_cache(cache)
            if cached is not None and len(cached):
                info["per_asset_from_2018_or_listing"][asset] = {
                    "rows": int(len(cached)),
                    "start": cached.index[0].isoformat(),
                    "end": cached.index[-1].isoformat(),
                    "cache_sha256": _sha256(cache),
                }
        if frames:
            info["joint_start"] = min(df.index[0] for df in frames.values()).isoformat()
            info["joint_end"] = min(df.index[-1] for df in frames.values()).isoformat()
            info["joint_rows"] = int(min(len(df) for df in frames.values()))
        body["intervals"][label] = info
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return body


def build_panels(*, use_network: bool = True, now: datetime | None = None) -> dict:
    panels = {}
    for label, minutes in INTERVALS.items():
        panels[label] = build_interval(minutes, now=now, use_network=use_network)
    # Cross-check the 4h panel's daily closes when research/closes.csv is present.
    cross = cross_check_closes(panels.get("4h") or {})
    samples = write_sample(panels)
    manifest = write_manifest(panels, cross, samples)
    return {"panels": panels, "cross_check": cross, "manifest": manifest, "sample": samples}


def wide_close(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame({a: df["close"] for a, df in frames.items()}).sort_index()


def wide_volume(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame({a: df["volume"] for a, df in frames.items()}).sort_index()


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Build cached Kraken 1h/4h panels (public data only)")
    ap.add_argument("--offline", action="store_true", help="use only local caches and zips")
    args = ap.parse_args()
    result = build_panels(use_network=not args.offline)
    for label, frames in result["panels"].items():
        for asset, df in frames.items():
            print(f"{label} {asset} {len(df)} {df.index[0]} -> {df.index[-1]}")
    print(json.dumps({"cross_check": result["cross_check"], "sample": result["sample"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
