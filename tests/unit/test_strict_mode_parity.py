import tempfile
import os

from src.parsers.fixed_width_parser import FixedWidthParser
from src.parsers.enhanced_validator import EnhancedFileValidator
from src.parsers.chunked_parser import ChunkedFixedWidthParser
from src.parsers.chunked_validator import ChunkedFileValidator


def _mapping():
    return {
        'fields': [
            {'name': 'ACCOUNT', 'position': 1, 'length': 4, 'required': True, 'format': '9(4)'},
            {'name': 'STATUS', 'position': 5, 'length': 1, 'required': False, 'valid_values': ['A', 'B']},
        ],
        'total_record_length': 5,
    }


def _codes_by_field(errors):
    out = set()
    for e in errors:
        if isinstance(e, dict):
            out.add((e.get('field'), e.get('code')))
    return out


def _strict_data_codes(errors):
    allowed = {'FW_REQ_001', 'FW_VAL_001', 'FW_FMT_001'}
    return {(f, c) for (f, c) in _codes_by_field(errors) if c in allowed}


def test_strict_mode_chunked_nonchunked_parity_required_and_format():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('1234Z\n')   # STATUS invalid enum -> FW_VAL_001
        f.write('    A\n')   # ACCOUNT empty required -> FW_REQ_001
        path = f.name

    try:
        mapping = _mapping()

        # non-chunked
        parser = FixedWidthParser(path, [('ACCOUNT', 0, 4), ('STATUS', 4, 5)])
        validator = EnhancedFileValidator(parser, mapping)
        nonchunked = validator.validate(detailed=False, strict_fixed_width=True, strict_level='format')

        # chunked
        chunk_parser = ChunkedFixedWidthParser(path, [('ACCOUNT', 0, 4), ('STATUS', 4, 5)], chunk_size=10)
        chunked_validator = ChunkedFileValidator(
            file_path=path,
            parser=chunk_parser,
            strict_fixed_width=True,
            strict_level='format',
            strict_fields=mapping['fields'],
            chunk_size=10,
            workers=1,
        )
        chunked = chunked_validator.validate_with_schema(
            expected_columns=['ACCOUNT', 'STATUS'],
            required_columns=['ACCOUNT'],
            show_progress=False,
        )

        assert _strict_data_codes(nonchunked['errors']) == _strict_data_codes(chunked['errors'])
    finally:
        os.unlink(path)
