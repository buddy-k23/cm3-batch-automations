"""Convert the hand-authored Batch Header mapping CSV to JSON."""
from src.config.template_converter import TemplateConverter

c = TemplateConverter()
c.from_csv(
    "mappings/csv/shaw_tranert/SHAW_TRANERT_BATCH_HEADER_mapping.csv",
    mapping_name="SHAW_TRANERT_BATCH_HEADER_mapping",
    file_format="fixed_width",
)
c.save("config/mappings/SHAW_TRANERT_BATCH_HEADER_mapping.json")
print("saved config/mappings/SHAW_TRANERT_BATCH_HEADER_mapping.json")
