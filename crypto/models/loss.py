"""Differentiable cost-aware Sharpe on net portfolio log-returns.

Not a price MSE. ``earned_returns[t]`` is the simple asset return the weight
row already earns (the next bar after the signal). Turnover is charged per side.
"""

from __future__ import annotations

import torch


def net_simple(
    weights: torch.Tensor,
    earned_returns: torch.Tensor,
    cost_rate: float,
) -> torch.Tensor:
    risky = weights[:, :-1]
    zeros = torch.zeros(1, risky.shape[-1], device=risky.device, dtype=risky.dtype)
    prev = torch.cat([zeros, risky[:-1].detach()], dim=0)
    gross = (risky * earned_returns).sum(dim=-1)
    turnover = (risky - prev).abs().sum(dim=-1)
    return gross - turnover * float(cost_rate)


def cost_aware_sharpe_loss(
    weights: torch.Tensor,
    earned_returns: torch.Tensor,
    cost_rate: float,
    downside_lambda: float = 0.5,
) -> torch.Tensor:
    net = net_simple(weights, earned_returns, cost_rate).clamp(min=-0.999999)
    log_r = torch.log1p(net)
    mu = log_r.mean()
    sd = torch.sqrt(((log_r - mu) ** 2).mean() + 1e-8)
    sharpe = mu / sd
    downside = torch.relu(-log_r).pow(2).mean()
    return -sharpe + float(downside_lambda) * downside
