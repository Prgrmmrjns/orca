"""Loss sign, weight caps, and a tiny forward / ONNX export."""

from __future__ import annotations

import numpy as np
import pytest
import torch

from crypto.models.loss import cost_aware_sharpe_loss, net_simple
from crypto.models.patchtst import PatchTST
from crypto.models.tcn import CausalTCN
from crypto.models.weights import finalize_weights
from crypto.validation.metrics import simulate_net_simple

torch = pytest.importorskip("torch")


def test_loss_prefers_the_aligned_book_and_penalises_cost():
    torch.manual_seed(0)
    earned = torch.full((48, 1), 0.01)
    good = torch.tensor([[0.8, 0.2]]).repeat(48, 1).requires_grad_(True)
    cash = torch.tensor([[0.0, 1.0]]).repeat(48, 1)
    good_loss = cost_aware_sharpe_loss(good, earned, cost_rate=0.001)
    cash_loss = cost_aware_sharpe_loss(cash, earned, cost_rate=0.001)
    assert good_loss < cash_loss
    costly = cost_aware_sharpe_loss(good, earned, cost_rate=0.05)
    cheap = cost_aware_sharpe_loss(good, earned, cost_rate=0.0)
    assert costly > cheap
    good_loss.backward()
    assert good.grad is not None
    assert torch.isfinite(good.grad).all()


def test_torch_net_matches_numpy_delay():
    risky = np.array([[0.5, 0.1], [0.2, 0.2], [0.0, 0.4], [0.1, 0.0]])
    cash = 1.0 - risky.sum(axis=1, keepdims=True)
    bar = np.array([[0.01, -0.02], [0.03, 0.0], [-0.01, 0.04], [0.02, 0.01]])
    weights = torch.tensor(np.concatenate([risky[:-1], cash[:-1]], axis=1), dtype=torch.float64)
    earned = torch.tensor(bar[1:], dtype=torch.float64)
    got = net_simple(weights, earned, cost_rate=0.01).detach().numpy()
    ref = simulate_net_simple(risky, bar, cost_rate=0.01)
    assert got == pytest.approx(ref)


def test_weight_cap_vol_scale_and_band():
    logits = torch.tensor([[8.0, 0.0, 0.0, 0.0, 0.0]])
    vol = torch.tensor([[0.20, 0.20, 0.20, 0.20]])
    prev = torch.zeros(1, 4)
    w = finalize_weights(logits, vol, prev)
    assert w.shape == (1, 5)
    assert torch.allclose(w.sum(-1), torch.ones(1), atol=1e-5)
    assert torch.all(w[:, :-1] <= 0.50 + 1e-5)
    assert torch.all(w >= -1e-5)
    assert w[0, 0] == pytest.approx(0.50, abs=1e-4)
    # High vol scales the sleeve down toward the 40% target.
    hot = finalize_weights(logits, torch.tensor([[2.0, 2.0, 2.0, 2.0]]), prev)
    assert hot[0, 0] < w[0, 0]
    # A 2% nudge inside the 5% band is ignored. An exit to zero is not.
    nudged = finalize_weights(logits, vol, torch.tensor([[0.48, 0.0, 0.0, 0.0]]))
    assert nudged[0, 0] == pytest.approx(0.48, abs=1e-4)
    exited = finalize_weights(torch.tensor([[-8.0, -8.0, -8.0, -8.0, 8.0]]), vol, torch.tensor([[0.40, 0.0, 0.0, 0.0]]))
    assert exited[0, 0] == pytest.approx(0.0, abs=1e-4)


def test_tiny_forward_and_onnx_roundtrip(tmp_path):
    torch.manual_seed(1)
    n_assets, n_features, lookback = 4, 11, 32
    x = torch.randn(2, lookback, n_assets, n_features)
    vol = torch.full((2, n_assets), 0.5)
    prev = torch.zeros(2, n_assets)
    for cls in (PatchTST, CausalTCN):
        model = cls(n_assets, n_features, lookback=lookback, n_heads=4, n_layers=1, d_model=32) if cls is PatchTST else cls(
            n_assets, n_features, lookback=lookback, channels=16, n_layers=2, n_heads=4
        )
        model.eval()
        w = model(x, vol, prev)
        assert w.shape == (2, n_assets + 1)
        assert torch.allclose(w.sum(-1), torch.ones(2), atol=1e-4)
        assert torch.all(w[:, :-1] <= 0.50 + 1e-4)
    onnx = pytest.importorskip("onnxruntime")
    model = PatchTST(n_assets, n_features, lookback=lookback, d_model=32, n_heads=4, n_layers=1)
    model.eval()
    path = tmp_path / "tiny.onnx"
    kwargs = dict(input_names=["features", "vol", "prev_weights"], output_names=["weights"], opset_version=17, dynamo=False)
    try:
        torch.onnx.export(model, (x[:1], vol[:1], prev[:1]), str(path), **kwargs)
    except TypeError:
        kwargs.pop("dynamo")
        torch.onnx.export(model, (x[:1], vol[:1], prev[:1]), str(path), **kwargs)
    sess = onnx.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    out = sess.run(None, {
        "features": x[:1].numpy(),
        "vol": vol[:1].numpy(),
        "prev_weights": prev[:1].numpy(),
    })[0]
    assert out.shape == (1, n_assets + 1)
    assert out.sum() == pytest.approx(1.0, abs=1e-4)
