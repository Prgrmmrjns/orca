"""PatchTST and causal TCN portfolio models."""

from crypto.models.loss import cost_aware_sharpe_loss
from crypto.models.patchtst import PatchTST
from crypto.models.tcn import CausalTCN
from crypto.models.weights import finalize_weights

def build_model(kind: str, n_assets: int, n_features: int, **hp):
    if kind == "patchtst":
        return PatchTST(n_assets, n_features, **hp)
    if kind == "tcn":
        return CausalTCN(n_assets, n_features, **hp)
    raise ValueError(f"unknown model {kind}")


__all__ = [
    "CausalTCN",
    "PatchTST",
    "build_model",
    "cost_aware_sharpe_loss",
    "finalize_weights",
]
