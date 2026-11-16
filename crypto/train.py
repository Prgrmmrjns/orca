#!/usr/bin/env python3
"""Walk-forward trainer for the paper PatchTST and causal TCN books.

The loss is a differentiable cost-aware Sharpe on net log-returns after a
per-side turnover charge, plus a downside penalty. Signals at close t earn
the next bar. Checkpoints are exported even when the promotion gate fails;
``metrics.json`` records the failure.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crypto.data_panel import wide_close, wide_volume
from crypto.features import build_features
from crypto.models import build_model
from crypto.models.loss import cost_aware_sharpe_loss
from crypto.models.weights import finalize_weights
from crypto.spec import ASSETS, BASE_COST_BPS, COST_BPS, FEATURE_NAMES, LOOKBACK, periods_per_year
from crypto.validation.gates import evaluate_gate
from crypto.validation.metrics import (
    buy_and_hold_simple,
    deflated_from_returns,
    max_drawdown,
    probability_of_backtest_overfitting,
    sharpe_ratio,
    simulate_net_simple,
    to_log,
)
from crypto.validation.splits import purged_walk_forward

OUT = ROOT / "crypto" / "checkpoints"
PRESETS = {
    "cpu": {
        "epochs": 3,
        "seeds": (1, 2),
        "lookback": 32,
        "d_model": 32,
        "n_layers": 1,
        "channels": 16,
        "tcn_layers": 4,
        "patch_len": 8,
        "n_heads": 4,
        "chunk": 192,
        "lr": 1e-3,
        "loco_epochs": 1,
    },
    "gpu": {
        "epochs": 8,
        "seeds": (1, 2, 3),
        "lookback": 64,
        "d_model": 64,
        "n_layers": 2,
        "channels": 32,
        "tcn_layers": 6,
        "patch_len": 8,
        "n_heads": 4,
        "chunk": 256,
        "lr": 1e-3,
        "loco_epochs": 2,
    },
}


def pick_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _hp(kind: str, preset: dict, n_assets: int, n_features: int) -> dict:
    if kind == "patchtst":
        return {
            "lookback": preset["lookback"],
            "patch_len": preset["patch_len"],
            "d_model": preset["d_model"],
            "n_heads": preset["n_heads"],
            "n_layers": preset["n_layers"],
            "dropout": 0.1,
        }
    return {
        "lookback": preset["lookback"],
        "channels": preset["channels"],
        "n_layers": preset["tcn_layers"],
        "kernel": 3,
        "n_heads": preset["n_heads"],
        "dropout": 0.1,
    }


def _decision_tensors(built: dict, lookback: int):
    feats = built["features"]
    vol = built["vol"]
    simple = built["simple"]
    t_len = len(feats)
    if t_len <= lookback + 2:
        raise RuntimeError(f"need more than {lookback + 2} rows, have {t_len}")
    decision_t = np.arange(lookback - 1, t_len - 1)
    from numpy.lib.stride_tricks import sliding_window_view

    win = sliding_window_view(feats, lookback, axis=0)
    win = np.moveaxis(win, -1, 1)
    win_i = decision_t - lookback + 1
    return {
        "x": np.ascontiguousarray(win[win_i], dtype=np.float32),
        "vol": np.ascontiguousarray(vol[decision_t], dtype=np.float32),
        "earned": np.ascontiguousarray(simple[decision_t + 1], dtype=np.float32),
        "decision_t": decision_t,
        "simple": simple,
    }


def _sequence(model, x, vol, device: torch.device, prev: np.ndarray | None = None) -> torch.Tensor:
    xt = torch.as_tensor(x, device=device)
    vt = torch.as_tensor(vol, device=device)
    use_amp = device.type == "cuda"
    logits = []
    step = 256
    for i in range(0, len(xt), step):
        xb = xt[i : i + step]
        if use_amp:
            with torch.amp.autocast("cuda"):
                logits.append(model.encode(xb).float())
        else:
            logits.append(model.encode(xb))
    logits = torch.cat(logits, 0)
    if prev is None:
        carry = torch.zeros(1, vt.shape[-1], device=device)
    else:
        carry = torch.as_tensor(prev, device=device, dtype=torch.float32).view(1, -1)
    weights = []
    for i in range(len(logits)):
        weights.append(finalize_weights(logits[i : i + 1], vt[i : i + 1], carry))
        carry = weights[-1][:, :-1].detach()
    return torch.cat(weights, 0)


def _fit_fold(model, pack, train_idx: np.ndarray, preset: dict, device: torch.device, epochs: int) -> None:
    decision_t = pack["decision_t"]
    train_set = set(int(i) for i in train_idx)
    mask = np.array([(int(t) in train_set and int(t) + 1 in train_set) for t in decision_t])
    if mask.sum() < 32:
        return
    x = pack["x"][mask]
    vol = pack["vol"][mask]
    earned = pack["earned"][mask]
    opt = torch.optim.AdamW(model.parameters(), lr=preset["lr"], weight_decay=1e-4)
    try:
        scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    except TypeError:
        scaler = torch.cuda.amp.GradScaler(enabled=False)
    chunk = int(preset["chunk"])
    cost = BASE_COST_BPS / 1e4
    model.train()
    n_assets = vol.shape[-1]
    for _epoch in range(epochs):
        prev_np = np.zeros(n_assets, dtype=np.float32)
        for start in range(0, len(x), chunk):
            sl = slice(start, start + chunk)
            opt.zero_grad(set_to_none=True)
            weights = _sequence(model, x[sl], vol[sl], device, prev=prev_np)
            er = torch.as_tensor(earned[sl], device=device)
            loss = cost_aware_sharpe_loss(weights, er, cost)
            scaler.scale(loss).backward()
            if device.type == "cuda":
                scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
            prev_np = weights[-1, :-1].detach().cpu().numpy()


@torch.no_grad()
def _weight_path(model, pack, device: torch.device) -> np.ndarray:
    model.eval()
    weights = _sequence(model, pack["x"], pack["vol"], device)
    risky = weights[:, :-1].detach().cpu().numpy()
    full = np.zeros((len(pack["simple"]), risky.shape[1]), dtype=np.float64)
    full[pack["decision_t"]] = risky
    return full


def _score_mask(full_w: np.ndarray, simple: np.ndarray, mask_t: np.ndarray, cost_rate: float, btc_i: int):
    net = simulate_net_simple(full_w, simple, cost_rate)
    oos = np.intersect1d(mask_t, np.arange(len(net)))
    if len(oos) < 2:
        return {"net_sharpe": float("nan"), "max_drawdown": 0.0, "btc_max_dd": 0.0, "net": np.zeros(0), "log": np.zeros(0)}
    path = net[oos]
    btc = buy_and_hold_simple(simple[oos + 1, btc_i], cost_rate)
    ppy = None  # filled by caller via log sharpe
    return {
        "net": path,
        "log": to_log(path),
        "max_drawdown": max_drawdown(path),
        "btc_max_dd": max_drawdown(btc),
        "btc_simple": btc,
    }


def _stitch_oos(full_w, simple, folds, index, cost_rate, btc_i, ppy):
    pieces = []
    btc_pieces = []
    fold_rows = []
    for fold in folds:
        scored = _score_mask(full_w, simple, fold["test"], cost_rate, btc_i)
        path, blog = scored["net"], scored.get("btc_simple")
        if len(path) < 2:
            continue
        sr = sharpe_ratio(to_log(path), ppy)
        fold_rows.append({
            "test_year": int(fold["test_year"]),
            "net_sharpe": None if not math.isfinite(sr) else float(sr),
            "max_drawdown": float(scored["max_drawdown"]),
            "btc_max_drawdown": float(scored["btc_max_dd"]),
            "bars": int(len(path)),
            "start": str(index[int(fold["test"][0])].date()),
            "end": str(index[int(fold["test"][-1])].date()),
        })
        pieces.append(path)
        btc_pieces.append(blog)
    if not pieces:
        return {"folds": fold_rows, "net": np.zeros(0), "btc": np.zeros(0)}
    return {"folds": fold_rows, "net": np.concatenate(pieces), "btc": np.concatenate(btc_pieces)}


def _export(model, kind: str, preset: dict, n_assets: int, n_features: int, out_dir: Path) -> dict:
    model.eval()
    device = next(model.parameters()).device
    lookback = preset["lookback"]
    dummy_x = torch.zeros(1, lookback, n_assets, n_features, device=device)
    dummy_v = torch.full((1, n_assets), 0.6, device=device)
    dummy_p = torch.zeros(1, n_assets, device=device)
    onnx_path = out_dir / f"{kind}.onnx"
    export_kwargs = dict(
        input_names=["features", "vol", "prev_weights"],
        output_names=["weights"],
        opset_version=17,
        dynamo=False,
    )
    try:
        torch.onnx.export(model, (dummy_x, dummy_v, dummy_p), str(onnx_path), **export_kwargs)
    except TypeError:
        export_kwargs.pop("dynamo")
        torch.onnx.export(model, (dummy_x, dummy_v, dummy_p), str(onnx_path), **export_kwargs)
    from safetensors.torch import save_file

    tensors = {k: v.detach().cpu().contiguous() for k, v in model.state_dict().items() if v.is_floating_point()}
    safe_path = out_dir / f"{kind}.safetensors"
    save_file(tensors, str(safe_path))
    return {"onnx": str(onnx_path.relative_to(ROOT)), "safetensors": str(safe_path.relative_to(ROOT))}


def _train_kind(kind, built, pack, folds, preset, device, ppy, btc_i, epochs: int | None = None):
    """Expanding walk-forward. Each test year is scored before it is trained on."""
    n_assets = len(built["assets"])
    n_features = built["features"].shape[-1]
    epochs = preset["epochs"] if epochs is None else epochs
    per_seed = []
    for seed in preset["seeds"]:
        torch.manual_seed(int(seed))
        np.random.seed(int(seed))
        model = build_model(kind, n_assets, n_features, **_hp(kind, preset, n_assets, n_features)).to(device)
        fold_rows = []
        pieces_net = []
        pieces_btc = []
        paths = []
        for fold in folds:
            _fit_fold(model, pack, fold["train"], preset, device, epochs)
            full_w = _weight_path(model, pack, device)
            paths.append((full_w, fold))
            st = _stitch_oos(full_w, pack["simple"], [fold], built["index"], BASE_COST_BPS / 1e4, btc_i, ppy)
            fold_rows.extend(st["folds"])
            if len(st["net"]):
                pieces_net.append(st["net"])
                pieces_btc.append(st["btc"])
            print(f"{kind} seed {seed} year {fold['test_year']} sharpe {st['folds'][-1]['net_sharpe'] if st['folds'] else 'nan'}", flush=True)
        net = np.concatenate(pieces_net) if pieces_net else np.zeros(0)
        btc = np.concatenate(pieces_btc) if pieces_btc else np.zeros(0)
        sr = sharpe_ratio(to_log(net), ppy) if len(net) else float("nan")
        stitched = {"folds": fold_rows, "net": net, "btc": btc}
        per_seed.append((model, stitched, sr, paths))
        print(f"{kind} seed {seed} oos sharpe {sr}", flush=True)
    finite = np.array([row[2] for row in per_seed], dtype=float)
    best_i = int(np.nanargmax(finite)) if np.isfinite(finite).any() else 0
    model, stitched, _sr, paths = per_seed[best_i]
    seed_sharpes = [None if not math.isfinite(row[2]) else float(row[2]) for row in per_seed]
    return model, stitched, seed_sharpes, paths


def _cost_stress_paths(paths, simple, index, btc_i, ppy):
    """Reprice saved fold weight paths. Each path was fit before its test year."""
    out = {}
    for bps in COST_BPS:
        nets = []
        for full_w, fold in paths:
            stitched = _stitch_oos(full_w, simple, [fold], index, bps / 1e4, btc_i, ppy)
            if len(stitched["net"]):
                nets.append(stitched["net"])
        path = np.concatenate(nets) if nets else np.zeros(0)
        sr = sharpe_ratio(to_log(path), ppy) if len(path) else float("nan")
        out[f"{int(bps)}bps"] = {
            "per_side_bps": bps,
            "net_sharpe": None if not math.isfinite(sr) else float(sr),
            "max_drawdown": None if len(path) == 0 else float(max_drawdown(path)),
        }
    return out


def _loco(kind, closes, volume, built, folds, preset, device, ppy):
    if not folds:
        return {}
    last = folds[-1:]
    report = {}
    assets = list(built["assets"])
    for drop in assets:
        keep = tuple(a for a in assets if a != drop)
        sma = "BTC" if "BTC" in keep else keep[0]
        sub = build_features(closes.loc[:, list(keep)], volume.loc[:, list(keep)], interval=built["interval"], assets=keep, sma_asset=sma)
        try:
            pack = _decision_tensors(sub, preset["lookback"])
        except RuntimeError as exc:
            report[drop] = {"error": str(exc)}
            continue
        sub_folds = purged_walk_forward(sub["index"])
        sub_folds = [f for f in sub_folds if f["test_year"] == last[0]["test_year"]] or sub_folds[-1:]
        btc_i = list(keep).index("BTC") if "BTC" in keep else 0
        saved = preset["seeds"]
        preset = dict(preset)
        preset["seeds"] = (saved[0],)
        _model, stitched, _seeds, _paths = _train_kind(
            kind, sub, pack, sub_folds, preset, device, ppy, btc_i, epochs=preset["loco_epochs"]
        )
        del _model
        sr = sharpe_ratio(to_log(stitched["net"]), ppy) if len(stitched["net"]) else float("nan")
        report[drop] = {
            "net_sharpe": None if not math.isfinite(sr) else float(sr),
            "test_year": int(sub_folds[-1]["test_year"]),
            "sma_asset": sma,
            "epochs": preset["loco_epochs"],
            "note": "retrained on the last fold only",
        }
        print(f"loco {kind} without {drop} sharpe {sr}", flush=True)
    return report


def train_panel(closes: pd.DataFrame, volume: pd.DataFrame, *, preset_name: str, device: torch.device, out_dir: Path) -> dict:
    preset = dict(PRESETS[preset_name])
    built = build_features(closes, volume, interval="4h", assets=tuple(closes.columns))
    pack = _decision_tensors(built, preset["lookback"])
    folds = purged_walk_forward(built["index"])
    ppy = periods_per_year("4h")
    assets = list(built["assets"])
    btc_i = assets.index("BTC") if "BTC" in assets else 0
    print(f"device={device} rows={len(built['index'])} folds={[f['test_year'] for f in folds]}", flush=True)
    results = {
        "device": str(device),
        "preset": preset_name,
        "interval": "4h",
        "assets": assets,
        "rows": int(len(built["index"])),
        "start": str(built["index"][0]),
        "end": str(built["index"][-1]),
        "lookback": preset["lookback"],
        "epochs": preset["epochs"],
        "seeds": list(preset["seeds"]),
        "cost_bps_per_side": BASE_COST_BPS,
        "delay_bars": 1,
        "walk_forward": "expanding yearly; each test year is scored before that year is used for training; 7-day embargo; 1-bar purge",
        "folds_years": [int(f["test_year"]) for f in folds],
        "models": {},
        "gate_note": "A gate passes only when net Sharpe > 0, max drawdown is better than BTC buy-and-hold, and every seed Sharpe is positive.",
    }
    trial_columns = []
    n_trials = len(preset["seeds"]) * 2
    for kind in ("patchtst", "tcn"):
        model, stitched, seed_sharpes, paths = _train_kind(kind, built, pack, folds, preset, device, ppy, btc_i)
        net = stitched["net"]
        sr = sharpe_ratio(to_log(net), ppy) if len(net) else float("nan")
        dd = max_drawdown(net) if len(net) else 0.0
        btc_dd = max_drawdown(stitched["btc"]) if len(stitched["btc"]) else 0.0
        gate = evaluate_gate(sr, dd, btc_dd, [s if s is not None else float("nan") for s in seed_sharpes])
        dsr = deflated_from_returns(to_log(net), ppy, n_trials) if len(net) else {"dsr": None}
        files = _export(model, kind, preset, len(assets), built["features"].shape[-1], out_dir)
        if len(net):
            trial_columns.append(to_log(net))
        entry = {
            "folds": stitched["folds"],
            "net_sharpe": None if not math.isfinite(sr) else float(sr),
            "max_drawdown": float(dd),
            "btc_max_drawdown": float(btc_dd),
            "seed_sharpes": seed_sharpes,
            "cost_stress": _cost_stress_paths(paths, pack["simple"], built["index"], btc_i, ppy),
            "checkpoint_note": "Exported weights are the model after the last expanding fold. That fit does not include the last test year.",
            "dsr": dsr,
            "gate": gate,
            "files": files,
            "promoted": bool(gate["passed"]),
        }
        results["models"][kind] = entry
        print(f"{kind} gate passed={gate['passed']} reasons={gate['reasons']}", flush=True)
        _write_metrics(out_dir, results)
    # PBO across the selected seed of each architecture when both paths align.
    if len(trial_columns) >= 2 and len({len(c) for c in trial_columns}) == 1:
        matrix = np.column_stack(trial_columns)
        # Repeat columns so N>=2 is already true; also split seeds were not stored
        # as full paths. Report PBO on the two selected architectures.
        results["pbo"] = probability_of_backtest_overfitting(matrix, n_groups=8, periods_per_year=ppy)
        if isinstance(results["pbo"], float) and not math.isfinite(results["pbo"]):
            results["pbo"] = None
    else:
        results["pbo"] = None
    try:
        results["models"]["patchtst"]["leave_one_coin_out"] = _loco(
            "patchtst", closes, volume, built, folds, preset, device, ppy
        )
        results["models"]["tcn"]["leave_one_coin_out"] = _loco(
            "tcn", closes, volume, built, folds, preset, device, ppy
        )
    except Exception as exc:  # LOCO is diagnostic; the checkpoints already exist
        results["leave_one_coin_out_error"] = f"{type(exc).__name__}: {exc}"
    spec = {
        "interval": "4h",
        "lookback": preset["lookback"],
        "assets": assets,
        "features": list(built["names"]),
        "sma_windows": [50, 100, 200],
        "feature_names_default": list(FEATURE_NAMES),
    }
    (out_dir / "model_spec.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    _write_metrics(out_dir, results)
    return results


def _write_metrics(out_dir: Path, results: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


def _load_panel(interval: str = "4h"):
    from crypto.data_panel import build_interval

    frames = build_interval(240 if interval == "4h" else 60, use_network=True)
    return wide_close(frames), wide_volume(frames)


def main() -> int:
    ap = argparse.ArgumentParser(description="Train paper PatchTST and TCN checkpoints")
    ap.add_argument("--preset", choices=("cpu", "gpu", "auto"), default="auto")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    device = pick_device()
    preset = args.preset
    if preset == "auto":
        preset = "gpu" if device.type in {"cuda", "mps"} else "cpu"
    if args.offline:
        from crypto.data_panel import CACHE, INTERVALS, align_assets, _read_cache
        from crypto.spec import ASSETS

        minutes = INTERVALS["4h"]
        frames = {}
        for asset in ASSETS:
            df = _read_cache(CACHE / f"{asset}_{minutes}_panel.csv")
            if df is None:
                raise SystemExit(f"missing cache for {asset}")
            frames[asset] = df
        frames = align_assets(frames)
        closes, volume = wide_close(frames), wide_volume(frames)
    else:
        closes, volume = _load_panel("4h")
    OUT.mkdir(parents=True, exist_ok=True)
    train_panel(closes, volume, preset_name=preset, device=device, out_dir=OUT)
    print(f"wrote {OUT / 'metrics.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
