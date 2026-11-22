"""LightGBM forecaster of next-7d return: CPU training, JSON export, numpy inference.

Live inference uses only numpy (trees are walked from the exported JSON), so the
paper server needs no lightgbm install and no GPU.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd

MODEL_PATH = Path(__file__).parent / "model" / "gbm.json"
LGB = dict(n_estimators=300, learning_rate=0.02, num_leaves=8, min_child_samples=200,
           subsample=0.7, subsample_freq=1, colsample_bytree=0.6, reg_lambda=5, verbose=-1, n_jobs=4)
SEEDS = (0, 1, 2)

def fit(X: pd.DataFrame, y: pd.Series):
    import lightgbm as lgb
    return [lgb.LGBMRegressor(random_state=s, **LGB).fit(X, y.clip(-0.5, 0.5)) for s in SEEDS]

def predict_lgb(models, X):
    return np.mean([m.predict(X) for m in models], axis=0)

def _flat(node, out):
    """Flatten a LightGBM tree dict into arrays; returns node index."""
    i = len(out["feat"])
    for k in out: out[k].append(0)
    if "leaf_value" in node:
        out["feat"][i] = -1; out["val"][i] = node["leaf_value"]
        return i
    assert node["decision_type"] == "<="
    out["feat"][i] = node["split_feature"]; out["val"][i] = node["threshold"]
    out["dleft"][i] = int(bool(node["default_left"]))
    out["left"][i] = _flat(node["left_child"], out)
    out["right"][i] = _flat(node["right_child"], out)
    return i

def export(models, features, path=MODEL_PATH, meta=None):
    trees = []
    for m in models:
        for t in m.booster_.dump_model()["tree_info"]:
            out = dict(feat=[], val=[], left=[], right=[], dleft=[])
            _flat(t["tree_structure"], out); trees.append(out)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(dict(features=list(features), n_models=len(models), trees=trees, meta=meta or {})))

class JsonModel:
    def __init__(self, path=MODEL_PATH):
        d = json.loads(Path(path).read_text())
        self.features, self.n_models, self.meta = d["features"], d["n_models"], d["meta"]
        self.trees = [{k: np.asarray(v, dtype=float if k == "val" else int) for k, v in t.items()} for t in d["trees"]]

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        A = X[self.features].to_numpy(float)
        total = np.zeros(len(A)); rows = np.arange(len(A))
        for t in self.trees:
            node = np.zeros(len(A), dtype=int)
            while True:
                f = t["feat"][node]; live = f >= 0
                if not live.any(): break
                x = A[rows, np.where(live, f, 0)]
                go_left = np.where(np.isnan(x), t["dleft"][node] == 1, x <= t["val"][node])
                node = np.where(live, np.where(go_left, t["left"][node], t["right"][node]), node)
            total += t["val"][node]
        return total / self.n_models
