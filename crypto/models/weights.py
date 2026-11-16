"""Final portfolio layer: softmax, cap, vol target, no-trade band."""

from __future__ import annotations

import torch

from crypto.spec import BAND, CORR_HAIRCUT, MAX_WEIGHT, TARGET_VOL


def finalize_weights(
    logits: torch.Tensor,
    vol: torch.Tensor,
    prev: torch.Tensor,
    *,
    target_vol: float = TARGET_VOL,
    max_weight: float = MAX_WEIGHT,
    band: float = BAND,
    corr_haircut: float = CORR_HAIRCUT,
) -> torch.Tensor:
    """Map logits to weights over assets + cash.

    ``logits`` is (B, A+1), ``vol`` is annualised per-asset vol (B, A),
    ``prev`` is the previous risky weight (B, A). The risky sleeve is capped
    at ``max_weight``, scaled down so estimated portfolio vol does not exceed
    ``target_vol``, then held still when the change is inside ``band`` unless
    the new weight is an exit to zero. Weights sum to one.
    """
    raw = torch.softmax(logits, dim=-1)
    asset = raw[:, :-1].clamp(max=max_weight)
    gross = asset.sum(dim=-1, keepdim=True)
    asset = torch.where(gross > 1.0, asset / gross.clamp(min=1e-8), asset)
    port_vol = (asset * vol.clamp(min=1e-6)).sum(dim=-1, keepdim=True) * corr_haircut
    scale = torch.where(
        port_vol > 0,
        (float(target_vol) / port_vol).clamp(max=1.0),
        torch.zeros_like(port_vol),
    )
    asset = asset * scale
    delta = asset - prev
    exiting = (asset <= 1e-6) & (prev > 1e-6)
    keep = (delta.abs() < band) & ~exiting
    asset = torch.where(keep, prev, asset)
    gross = asset.sum(dim=-1, keepdim=True)
    asset = torch.where(gross > 1.0, asset / gross.clamp(min=1e-8), asset)
    cash = 1.0 - asset.sum(dim=-1, keepdim=True)
    return torch.cat([asset, cash], dim=-1)
