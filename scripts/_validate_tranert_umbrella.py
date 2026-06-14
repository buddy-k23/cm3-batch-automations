"""Validate the SHAW_TRANERT.yaml umbrella loads cleanly."""
import yaml
from src.config.multi_record_config import MultiRecordConfig

with open("config/mappings/SHAW_TRANERT.yaml", encoding="utf-8") as f:
    raw = yaml.safe_load(f)

cfg = MultiRecordConfig(**raw)
print(f"discriminator: field={cfg.discriminator.field} pos={cfg.discriminator.position} len={cfg.discriminator.length}")
print(f"record_types ({len(cfg.record_types)}): {list(cfg.record_types.keys())}")
print(f"cross_type_rules ({len(cfg.cross_type_rules)}): {[r.check for r in cfg.cross_type_rules]}")
print(f"default_action: {cfg.default_action}")
print("OK — umbrella loads cleanly")
