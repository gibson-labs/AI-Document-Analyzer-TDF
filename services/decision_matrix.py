import os
from typing import Optional


def resolve_decision_matrix_path() -> str:
    raw = os.getenv("TDF_DECISION_MATRIX_PATH", "").strip()
    if raw:
        return raw
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "files", "Decision Matrix's.xlsx"))


def load_decision_matrix_weights(path: str) -> dict[str, float]:
    """Parse decision matrix weights from an XLSX file.

    Raises:
      FileNotFoundError: if the file doesn't exist
      ValueError: if weights cannot be parsed
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    import pandas as pd

    df = pd.read_excel(path, sheet_name=0)
    name_cols = [c for c in df.columns if str(c).strip().lower() in {"criterion", "criteria", "category", "factor"}]
    weight_cols = [c for c in df.columns if str(c).strip().lower() in {"weight", "weights", "%", "percentage", "score weight"}]

    if not name_cols or not weight_cols:
        raise ValueError("Could not detect criterion and weight columns in the decision matrix.")

    names = df[name_cols[0]].astype(str).str.strip()
    weights = df[weight_cols[0]]
    valid = weights.notna() & names.notna()
    names = names[valid]
    weights = weights[valid].astype(float)

    total = float(weights.sum())
    if total <= 0:
        raise ValueError("Decision matrix weights sum to zero or negative.")

    mapping = {str(n): float(w / total) for n, w in zip(names.tolist(), weights.tolist()) if str(n)}
    if not mapping:
        raise ValueError("Decision matrix produced no valid criteria.")
    return mapping


def maybe_load_weights(mode: str) -> Optional[dict[str, float]]:
    path = resolve_decision_matrix_path()
    if mode == "memo":
        return None
    if not os.path.exists(path):
        if mode == "weighted":
            raise FileNotFoundError(path)
        return None
    try:
        return load_decision_matrix_weights(path)
    except Exception:
        if mode == "weighted":
            raise
        return None
