"""Confirm expect values loaded from the TRANERT umbrella."""
import yaml
from src.config.multi_record_config import MultiRecordConfig

raw = yaml.safe_load(open("config/mappings/SHAW_TRANERT.yaml", encoding="utf-8").read())
cfg = MultiRecordConfig(**raw)
for name, rt in cfg.record_types.items():
    print(f"  {name}: expect={rt.expect}")
