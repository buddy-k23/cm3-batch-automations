"""Convert Excel/CSV templates to universal mapping format."""

import pandas as pd
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional


class TemplateConverter:
    """Convert Excel/CSV templates to universal mapping JSON."""
    
    # Expected column names in template
    REQUIRED_COLUMNS = ['Field Name', 'Data Type']
    OPTIONAL_COLUMNS = [
        'Position', 'Length', 'Format', 'Required',
        'Description', 'Default Value', 'Target Name', 'Valid Values',
        # ADR 0018: JSONPath selector locating each field in a JSON record
        # (e.g. ``$.customer.id``, ``$.transactions[*]``). Presence of a
        # populated value in this column switches the converter to the JSON
        # mapping shape (``json_path`` per field instead of position/length).
        'JSON Path',
        # ADR 0019: XPath selector locating each field in an XML record
        # (e.g. ``customer/id``, ``account/@id``, ``transactions/transaction``).
        # Presence of a populated value switches the converter to the XML
        # mapping shape (``xml_xpath`` per field). The XPath itself carries the
        # attribute-vs-element-text distinction (a trailing ``@name`` step).
        'XML XPath',
    ]
    
    def __init__(self, frozen_timestamp: str | None = None):
        """Initialize converter.

        Args:
            frozen_timestamp: Optional deterministic value to substitute
                for ``datetime.utcnow().isoformat() + "Z"`` in
                ``metadata.created_date`` and ``metadata.last_modified``.

                When ``None`` (default) the converter keeps its
                historical ``datetime.utcnow()`` behaviour so existing
                callers continue producing wall-clock-stamped artefacts
                with no behaviour change.

                When set to a string (e.g. ``"GENERATED"`` for tests,
                or a real ISO 8601 timestamp pulled from a committed
                artefact for ``--check`` round-trips) that exact string
                replaces both timestamp fields verbatim, making the
                emitted JSON byte-stable across runs (EC-S10).
        """
        self.mapping = None
        self.frozen_timestamp = frozen_timestamp
    
    def from_excel(self, excel_path: str, sheet_name: str = None, 
                   mapping_name: str = None, file_format: str = None) -> dict:
        """
        Convert Excel template to universal mapping.
        
        Args:
            excel_path: Path to Excel file
            sheet_name: Sheet name (uses first sheet if not specified)
            mapping_name: Name for the mapping (derived from filename if not specified)
            file_format: File format (auto-detected if not specified)
            
        Returns:
            Universal mapping dictionary
        """
        # Read Excel file.
        #
        # Per ADR 0007: ``dtype=str`` forces every cell to its literal
        # string form. Without it, pandas auto-infers numeric columns
        # so a cell containing the integer literal ``100030`` is read
        # as the float ``100030.0`` and ``str(...)`` downstream emits
        # ``'100030.0'`` into the JSON's ``valid_values``. Columns that
        # need numeric coercion (``Position``, ``Length``) already do
        # explicit ``int(...)`` in ``_convert_row_to_field``.
        if sheet_name:
            df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype=str)
        else:
            df = pd.read_excel(excel_path, sheet_name=0, dtype=str)
        
        return self._convert_dataframe(df, excel_path, mapping_name, file_format)
    
    def from_csv(self, csv_path: str, mapping_name: str = None, 
                 file_format: str = None) -> dict:
        """
        Convert CSV template to universal mapping.
        
        Args:
            csv_path: Path to CSV file
            mapping_name: Name for the mapping
            file_format: File format (auto-detected if not specified)
            
        Returns:
            Universal mapping dictionary
        """
        # Per ADR 0007: ``dtype=str`` preserves cell literals — see the
        # docstring block in ``from_excel`` for the full rationale.
        df = pd.read_csv(csv_path, dtype=str)
        return self._convert_dataframe(df, csv_path, mapping_name, file_format)

    def from_json_template(self, template_path: str,
                           mapping_name: str = None) -> dict:
        """Convert a CSV/Excel template carrying a ``JSON Path`` column to a JSON mapping.

        Identical in shape to :meth:`from_csv` / :meth:`from_excel`, but
        forces ``file_format='json'`` so the emitted mapping carries a
        ``json_path`` per field (the JSONPath selector
        :class:`~src.parsers.json_parser.JsonParser` resolves at parse time)
        instead of positional ``position`` / ``length`` anchors (ADR 0018).

        Args:
            template_path: Path to the ``.csv`` / ``.xlsx`` / ``.xls``
                template. The ``JSON Path`` column locates each field in a
                JSON record (e.g. ``$.customer.id``, ``$.transactions[*]``).
            mapping_name: Optional mapping name. Derived from the template
                filename stem when omitted.

        Returns:
            Universal mapping dict whose ``source.format`` is ``"json"`` and
            whose per-field entries carry ``json_path`` when the template
            cell is populated.
        """
        # Per ADR 0007: ``dtype=str`` preserves cell literals (see from_excel).
        suffix = Path(template_path).suffix.lower()
        if suffix in ('.xlsx', '.xls'):
            df = pd.read_excel(template_path, sheet_name=0, dtype=str)
        else:
            df = pd.read_csv(template_path, dtype=str)
        return self._convert_dataframe(
            df, template_path, mapping_name, file_format='json'
        )

    def from_xml_template(self, template_path: str,
                          mapping_name: str = None) -> dict:
        """Convert a CSV/Excel template carrying an ``XML XPath`` column to an XML mapping.

        Identical in shape to :meth:`from_csv` / :meth:`from_excel`, but forces
        ``file_format='xml'`` so the emitted mapping carries an ``xml_xpath``
        per field (the XPath selector
        :class:`~src.parsers.xml_parser.XmlParser` resolves at parse time)
        instead of positional ``position`` / ``length`` anchors (ADR 0019).
        The XPath itself carries the attribute-vs-element-text distinction —
        a trailing ``@name`` step (``account/@id``) reads the attribute.

        Args:
            template_path: Path to the ``.csv`` / ``.xlsx`` / ``.xls``
                template. The ``XML XPath`` column locates each field in an
                XML record (e.g. ``customer/id``, ``account/@id``,
                ``transactions/transaction``).
            mapping_name: Optional mapping name. Derived from the template
                filename stem when omitted.

        Returns:
            Universal mapping dict whose ``source.format`` is ``"xml"`` and
            whose per-field entries carry ``xml_xpath`` when the template cell
            is populated.
        """
        # Per ADR 0007: ``dtype=str`` preserves cell literals (see from_excel).
        suffix = Path(template_path).suffix.lower()
        if suffix in ('.xlsx', '.xls'):
            df = pd.read_excel(template_path, sheet_name=0, dtype=str)
        else:
            df = pd.read_csv(template_path, dtype=str)
        return self._convert_dataframe(
            df, template_path, mapping_name, file_format='xml'
        )

    def _convert_dataframe(self, df: pd.DataFrame, template_path: str,
                          mapping_name: str = None, file_format: str = None) -> dict:
        """Convert DataFrame to universal mapping."""
        
        # Clean column names
        df.columns = df.columns.str.strip()

        # Normalize snake_case columns to Title Case (e.g. field_name → Field Name)
        col_map = {
            'field_name': 'Field Name', 'data_type': 'Data Type',
            'position': 'Position', 'length': 'Length', 'format': 'Format',
            'required': 'Required', 'description': 'Description',
            'default_value': 'Default Value', 'target_name': 'Target Name',
            'valid_values': 'Valid Values', 'transformation': 'Transformation',
            'json_path': 'JSON Path', 'xml_xpath': 'XML XPath',
        }
        df.columns = [col_map.get(c.lower().replace(' ', '_'), c) for c in df.columns]

        # Validate required columns
        missing_cols = [col for col in self.REQUIRED_COLUMNS if col not in df.columns]
        if missing_cols:
            raise ValueError(f"Missing required columns: {missing_cols}")
        
        # Auto-detect format if not specified
        if not file_format:
            file_format = self._detect_format(df)
        
        # Generate mapping name if not specified
        if not mapping_name:
            mapping_name = Path(template_path).stem
        
        # Build mapping structure
        mapping = {
            "mapping_name": mapping_name,
            "version": "1.0.0",
            "description": f"Generated from template: {Path(template_path).name}",
            "source": {
                "type": "file",
                "format": file_format,
                "encoding": "UTF-8"
            },
            "target": {
                "type": "database"
            },
            "fields": [],
            "key_columns": [],
            "metadata": {
                "created_by": "template_converter",
                # Per EC-S10: a non-None ``frozen_timestamp`` replaces
                # the wall-clock ``datetime.utcnow()`` value so two
                # back-to-back conversions of the same template produce
                # byte-identical JSON. Default ``None`` preserves the
                # pre-EC-S10 behaviour (wall-clock UTC timestamp).
                "created_date": (
                    self.frozen_timestamp
                    if self.frozen_timestamp is not None
                    else datetime.utcnow().isoformat() + "Z"
                ),
                "last_modified": (
                    self.frozen_timestamp
                    if self.frozen_timestamp is not None
                    else datetime.utcnow().isoformat() + "Z"
                ),
                "source_template": str(template_path)
            }
        }
        
        # Add delimiter for delimited formats
        if file_format in ['pipe_delimited', 'csv', 'tsv']:
            if file_format == 'pipe_delimited':
                mapping['source']['delimiter'] = '|'
            elif file_format == 'csv':
                mapping['source']['delimiter'] = ','
            elif file_format == 'tsv':
                mapping['source']['delimiter'] = '\t'
        
        # Convert each row to field specification
        for idx, row in df.iterrows():
            field = self._convert_row_to_field(row, file_format)
            mapping['fields'].append(field)
            
            # Add to key columns if marked as required
            if field.get('required') and not mapping['key_columns']:
                mapping['key_columns'].append(field['name'])
        
        # Calculate total record length for fixed-width
        if file_format == 'fixed_width':
            total_length = sum(f.get('length', 0) for f in mapping['fields'])
            mapping['total_record_length'] = total_length

        # Warn about fixed-width fields that have no usable length value.
        warnings: list[str] = []
        if file_format == 'fixed_width':
            for field in mapping['fields']:
                field_name = field.get('name', '<unknown>')
                length_val = field.get('length')
                if length_val is None or length_val == 0:
                    warnings.append(
                        f"Field '{field_name}' has no length defined — "
                        "fixed-width parsing may produce incorrect results."
                    )
        mapping['warnings'] = warnings

        self.mapping = mapping
        return mapping
    
    def _detect_format(self, df: pd.DataFrame) -> str:
        """Auto-detect file format from template columns.

        A populated ``JSON Path`` column wins (ADR 0018), or a populated
        ``XML XPath`` column (ADR 0019): each unambiguously marks a nested
        mapping. Otherwise the historic position+length → fixed-width / else →
        pipe-delimited heuristic applies, so templates without either column
        remain backward-compatible.
        """
        # JSON takes precedence: a JSON Path column with at least one
        # non-blank cell means the BA authored JSONPath selectors.
        if 'JSON Path' in df.columns and df['JSON Path'].notna().any():
            non_blank = df['JSON Path'].astype(str).str.strip()
            if (non_blank != '').any():
                return 'json'

        # XML next: a populated XML XPath column means the BA authored XPath
        # selectors (ADR 0019).
        if 'XML XPath' in df.columns and df['XML XPath'].notna().any():
            non_blank = df['XML XPath'].astype(str).str.strip()
            if (non_blank != '').any():
                return 'xml'

        has_position = 'Position' in df.columns
        has_length = 'Length' in df.columns

        if has_position and has_length:
            return 'fixed_width'
        else:
            return 'pipe_delimited'  # Default to pipe-delimited
    
    def _convert_row_to_field(self, row: pd.Series, file_format: str) -> Dict[str, Any]:
        """Convert template row to field specification."""
        
        field = {
            "name": str(row['Field Name']).strip(),
            "data_type": self._normalize_data_type(str(row['Data Type']).strip())
        }
        
        # Add target name if specified
        if 'Target Name' in row and pd.notna(row['Target Name']):
            field['target_name'] = str(row['Target Name']).strip()
        
        # Add position and length for fixed-width
        if file_format == 'fixed_width':
            if 'Position' in row and pd.notna(row['Position']):
                field['position'] = int(row['Position'])
            if 'Length' in row and pd.notna(row['Length']):
                field['length'] = int(row['Length'])

        # Add the JSONPath selector for JSON mappings (ADR 0018). Only emit
        # when the cell is populated — a blank cell must not leak an empty
        # json_path key (the field is simply not located in the JSON record).
        if file_format == 'json' and 'JSON Path' in row and pd.notna(row['JSON Path']):
            json_path = str(row['JSON Path']).strip()
            if json_path:
                field['json_path'] = json_path

        # Add the XPath selector for XML mappings (ADR 0019). Only emit when the
        # cell is populated — a blank cell must not leak an empty xml_xpath key
        # (the field is simply not located in the XML record). An ``integer``
        # field whose XPath points at a repeated child collapses to a
        # ``<field>_count`` column in XmlParser; flag it with ``xml_array`` so
        # the parser does not depend solely on the data-type heuristic.
        if file_format == 'xml' and 'XML XPath' in row and pd.notna(row['XML XPath']):
            xml_xpath = str(row['XML XPath']).strip()
            if xml_xpath:
                field['xml_xpath'] = xml_xpath
                last_step = xml_xpath.split('/')[-1]
                if (
                    field['data_type'] == 'integer'
                    and not last_step.startswith('@')
                ):
                    field['xml_array'] = True

        # Add format if specified
        if 'Format' in row and pd.notna(row['Format']):
            field['format'] = str(row['Format']).strip()
        
        # Add required flag
        if 'Required' in row and pd.notna(row['Required']):
            required_val = str(row['Required']).strip().upper()
            field['required'] = required_val in ['Y', 'YES', 'TRUE', '1']
        else:
            field['required'] = False
        
        # Add description
        if 'Description' in row and pd.notna(row['Description']):
            field['description'] = str(row['Description']).strip()
        
        # Add default value
        if 'Default Value' in row and pd.notna(row['Default Value']):
            field['default_value'] = row['Default Value']
        
        # Add basic transformations
        field['transformations'] = [{"type": "trim"}]
        
        # Add basic validations
        field['validation_rules'] = []
        if field['required']:
            field['validation_rules'].append({"type": "not_null"})

        # Add allowed-values rule when provided
        if 'Valid Values' in row and pd.notna(row['Valid Values']):
            values_str = str(row['Valid Values']).strip()
            if values_str and not self._is_descriptive_text(values_str):
                # Support comma- or pipe-separated lists
                delimiter = '|' if '|' in values_str else ','
                valid_values = [v.strip() for v in values_str.split(delimiter) if v.strip()]
                if valid_values:
                    field['valid_values'] = valid_values
                    field['validation_rules'].append({
                        "type": "in_list",
                        "parameters": {"values": valid_values}
                    })

        return field
    
    @staticmethod
    def _is_descriptive_text(text: str) -> bool:
        """Return True if text is a description rather than actual valid values."""
        t = text.lower()
        skip_phrases = [
            'must be', 'is used', 'represents', 'starting', 'equal to',
            'table', 'control', 'see ', 'refer', 'each digit', 'cycle id',
            'if ', 'when ', 'the ', 'this ', 'valid loc', 'defined in',
        ]
        return any(phrase in t for phrase in skip_phrases) or len(text) > 60

    def _normalize_data_type(self, data_type: str) -> str:
        """Normalize data type to standard values."""
        data_type_lower = data_type.lower()
        
        if data_type_lower in ['string', 'str', 'text', 'varchar', 'char']:
            return 'string'
        elif data_type_lower in ['number', 'numeric', 'num', 'decimal', 'float']:
            return 'decimal'
        elif data_type_lower in ['integer', 'int']:
            return 'integer'
        elif data_type_lower in ['date', 'datetime', 'timestamp']:
            return 'date'
        elif data_type_lower in ['boolean', 'bool']:
            return 'boolean'
        else:
            return 'string'  # Default to string
    
    def save(self, output_path: str):
        """Save mapping to JSON file."""
        if not self.mapping:
            raise ValueError("No mapping to save. Convert a template first.")
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(self.mapping, f, indent=2)
        
        print(f"Mapping saved to: {output_path}")
    
    def print_summary(self):
        """Print summary of converted mapping."""
        if not self.mapping:
            print("No mapping loaded")
            return
        
        print(f"\nMapping Summary:")
        print(f"  Name: {self.mapping['mapping_name']}")
        print(f"  Format: {self.mapping['source']['format']}")
        print(f"  Fields: {len(self.mapping['fields'])}")
        print(f"  Key columns: {self.mapping.get('key_columns', [])}")
        
        if self.mapping['source']['format'] == 'fixed_width':
            print(f"  Total record length: {self.mapping.get('total_record_length', 0)} characters")
        
        print(f"\nFirst 5 fields:")
        for field in self.mapping['fields'][:5]:
            print(f"    {field['name']} ({field['data_type']})")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python template_converter.py <template_file> <output_json> [mapping_name] [format]")
        print("\nExamples:")
        print("  python template_converter.py template.xlsx mapping.json")
        print("  python template_converter.py template.csv mapping.json my_mapping fixed_width")
        sys.exit(1)
    
    template_file = sys.argv[1]
    output_file = sys.argv[2]
    mapping_name = sys.argv[3] if len(sys.argv) > 3 else None
    file_format = sys.argv[4] if len(sys.argv) > 4 else None
    
    converter = TemplateConverter()
    
    # Convert based on file extension
    if template_file.endswith('.xlsx') or template_file.endswith('.xls'):
        mapping = converter.from_excel(template_file, mapping_name=mapping_name, file_format=file_format)
    elif template_file.endswith('.csv'):
        mapping = converter.from_csv(template_file, mapping_name=mapping_name, file_format=file_format)
    else:
        print(f"Unsupported file format: {template_file}")
        sys.exit(1)
    
    # Print summary
    converter.print_summary()
    
    # Save to file
    converter.save(output_file)
    
    print(f"\n✓ Conversion complete!")
