"""Channel-independent PatchTST plus a small cross-asset attention mix."""

from __future__ import annotations

import torch
import torch.nn as nn

from crypto.models.weights import finalize_weights
from crypto.spec import LOOKBACK


class PatchTST(nn.Module):
    def __init__(
        self,
        n_assets: int,
        n_features: int,
        lookback: int = LOOKBACK,
        patch_len: int = 8,
        d_model: int = 32,
        n_heads: int = 4,
        n_layers: int = 1,
        dropout: float = 0.1,
    ):
        super().__init__()
        if lookback % patch_len != 0:
            raise ValueError(f"lookback {lookback} must be divisible by patch_len {patch_len}")
        if d_model % n_heads != 0:
            raise ValueError("d_model must be divisible by n_heads")
        self.n_assets = n_assets
        self.n_features = n_features
        self.lookback = lookback
        self.patch_len = patch_len
        self.n_patches = lookback // patch_len
        self.patch = nn.Linear(patch_len * n_features, d_model)
        layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=d_model * 2,
            dropout=dropout,
            batch_first=True,
            norm_first=True,
            activation="gelu",
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=n_layers, enable_nested_tensor=False)
        self.cross = nn.MultiheadAttention(d_model, n_heads, dropout=dropout, batch_first=True)
        self.asset_head = nn.Linear(d_model, 1)
        self.cash_head = nn.Linear(d_model, 1)

    def encode(self, features: torch.Tensor) -> torch.Tensor:
        # features: (B, L, A, F), all dates inside the window are <= decision time
        b, length, assets, feats = features.shape
        if assets != self.n_assets or feats != self.n_features or length != self.lookback:
            raise ValueError(
                f"expected (B, {self.lookback}, {self.n_assets}, {self.n_features}), got {tuple(features.shape)}"
            )
        x = features.permute(0, 2, 1, 3).reshape(b * assets, self.n_patches, self.patch_len * feats)
        h = self.encoder(self.patch(x))
        pooled = h[:, -1, :].reshape(b, assets, -1)
        mixed, _ = self.cross(pooled, pooled, pooled, need_weights=False)
        asset_logits = self.asset_head(mixed).squeeze(-1)
        cash_logit = self.cash_head(mixed.mean(dim=1))
        return torch.cat([asset_logits, cash_logit], dim=-1)

    def forward(self, features: torch.Tensor, vol: torch.Tensor, prev_weights: torch.Tensor) -> torch.Tensor:
        return finalize_weights(self.encode(features), vol, prev_weights)
