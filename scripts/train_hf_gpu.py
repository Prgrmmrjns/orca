#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy>=1.26",
#   "onnx>=1.16",
#   "pandas>=2.2",
#   "pyyaml>=6",
#   "safetensors>=0.4",
#   "torch>=2.2",
# ]
# ///
"""Train the paper crypto books and export ONNX + safetensors.

Same trainer as ``python -m crypto train``. Paper research only: this script
does not place orders and does not read an API key.

Local (CPU or whatever torch sees):

    uv run scripts/train_hf_gpu.py

Hugging Face Jobs, from a checkout of this repo so the ``crypto`` package is
on the image (A10G or L4). The script file alone is not enough; the job has
to contain the repository:

    hf jobs uv run --flavor a10g-small scripts/train_hf_gpu.py
    hf jobs uv run --flavor l4x1 scripts/train_hf_gpu.py

``--preset gpu`` is selected automatically when cuda or mps is available.
Outputs land in ``crypto/checkpoints/`` (``.onnx``, ``.safetensors``,
``metrics.json``). A failed promotion gate is written down; the best
checkpoint is still exported.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> int:
    import torch

    from crypto.train import main as train_main

    if len(sys.argv) == 1:
        gpu = torch.cuda.is_available() or (
            getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available()
        )
        sys.argv.append("--preset")
        sys.argv.append("gpu" if gpu else "cpu")
    return train_main()


if __name__ == "__main__":
    raise SystemExit(main())
