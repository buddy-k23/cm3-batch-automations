"""Integration checks for chunked business-rule execution + report model adaptation."""

import json
import os
import tempfile

from src.parsers.chunked_validator import ChunkedFileValidator
from src.reports.adapters.result_adapter_chunked import adapt_chunked_validation_result


def _tmp_file(content: str, suffix: str) -> str:
    f = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=suffix)
    f.write(content)
    f.close()
    return f.name


def test_chunked_business_rules_report_model_positive_and_negative():
    rules = {
        'metadata': {'name': 'chunked-rules-it'},
        'rules': [
            {
                'id': 'BR_IT_1',
                'name': 'score required when active',
                'type': 'field_validation',
                'severity': 'error',
                'field': 'score',
                'operator': 'not_null',
                'when': 'status = ACTIVE',
                'enabled': True,
            }
        ],
    }

    rules_path = _tmp_file(json.dumps(rules), '.json')
    pos_data = _tmp_file('status|score\nACTIVE|10\nINACTIVE|20\n', '.txt')
    neg_data = _tmp_file('status|score\nACTIVE|\nINACTIVE|20\n', '.txt')

    try:
        pos_validator = ChunkedFileValidator(pos_data, delimiter='|', chunk_size=1, rules_config_path=rules_path)
        pos_result = pos_validator.validate(show_progress=False)
        pos_model = adapt_chunked_validation_result(pos_result, file_path=pos_data)

        assert pos_model['business_rules']['enabled'] is True
        assert pos_model['business_rules']['statistics']['total_violations'] == 0

        neg_validator = ChunkedFileValidator(neg_data, delimiter='|', chunk_size=1, rules_config_path=rules_path)
        neg_result = neg_validator.validate(show_progress=False)
        neg_model = adapt_chunked_validation_result(neg_result, file_path=neg_data)

        assert neg_model['business_rules']['enabled'] is True
        assert neg_model['business_rules']['statistics']['total_violations'] >= 1
        assert len(neg_model['business_rules']['violations']) >= 1
    finally:
        os.unlink(rules_path)
        os.unlink(pos_data)
        os.unlink(neg_data)
