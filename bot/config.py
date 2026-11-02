from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
VALID_MODES = {"paper_local", "alpaca_paper", "alpaca_live"}


def load_config(path: str | Path = ROOT / "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    if cfg.get("mode") not in VALID_MODES:
        raise ValueError(f"mode debe ser uno de {sorted(VALID_MODES)}")
    weights = cfg["portfolio"]
    if not weights or any(w < 0 for w in weights.values()):
        raise ValueError("portfolio vacío o con pesos negativos")
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"los pesos de portfolio suman {total:.4f}, deben sumar 1.0")
    if cfg.get("defensive_asset") in weights:
        raise ValueError("defensive_asset no puede estar también en portfolio")


def all_symbols(cfg: dict) -> list[str]:
    syms = list(cfg["portfolio"])
    if cfg["strategy"].get("trend_filter"):
        syms.append(cfg["defensive_asset"])
    return syms
