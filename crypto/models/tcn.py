"""Causal dilated temporal convolution, then a small cross-asset mix."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from crypto.models.weights import finalize_weights
from crypto.spec import LOOKBACK


class CausalConv1d(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, kernel: int, dilation: int):
        super().__init__()
        self.pad = (kernel - 1) * dilation
        self.conv = nn.Conv1d(in_ch, out_ch, kernel_size=kernel, dilation=dilation)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv(F.pad(x, (self.pad, 0)))


class CausalTCN(nn.Module):
    def __init__(
        self,
        n_assets: int,
        n_features: int,
        lookback: int = LOOKBACK,
        channels: int = 16,
        n_layers: int = 4,
        kernel: int = 3,
        n_heads: int = 4,
        dropout: float = 0.1,
    ):
        super().__init__()
        if channels % n_heads != 0:
            raise ValueError("channels must be divisible by n_heads")
        self.n_assets = n_assets
        self.n_features = n_features
        self.lookback = lookback
        self.in_proj = CausalConv1d(n_features, channels, kernel, dilation=1)
        self.blocks = nn.ModuleList(
            [CausalConv1d(channels, channels, kernel, dilation=2**i) for i in range(n_layers)]
        )
        self.drop = nn.Dropout(dropout)
        self.cross = nn.MultiheadAttention(channels, n_heads, dropout=dropout, batch_first=True)
        self.asset_head = nn.Linear(channels, 1)
        self.cash_head = nn.Linear(channels, 1)

    def encode(self, features: torch.Tensor) -> torch.Tensor:
        b, length, assets, feats = features.shape
        if assets != self.n_assets or feats != self.n_features or length != self.lookback:
            raise ValueError(
                f"expected (B, {self.lookback}, {self.n_assets}, {self.n_features}), got {tuple(features.shape)}"
            )
        x = features.permute(0, 2, 3, 1).reshape(b * assets, feats, length)
        h = F.gelu(self.in_proj(x))
        for block in self.blocks:
            h = h + self.drop(F.gelu(block(h)))
        last = h[:, :, -1].reshape(b, assets, -1)
        mixed, _ = self.cross(last, last, last, need_weights=False)
        asset_logits = self.asset_head(mixed).squeeze(-1)
        cash_logit = self.cash_head(mixed.mean(dim=1))
        return torch.cat([asset_logits, cash_logit], dim=-1)

    def forward(self, features: torch.Tensor, vol: torch.Tensor, prev_weights: torch.Tensor) -> torch.Tensor:
        return finalize_weights(self.encode(features), vol, prev_weights)
