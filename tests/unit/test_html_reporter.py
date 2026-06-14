"""Unit tests for HTML reporter."""

import pytest
import tempfile
import os
from src.reports.renderers.comparison_renderer import HTMLReporter


class TestHTMLReporter:
    """Test HTMLReporter class."""

    def test_generate_report(self):
        """Test HTML report generation."""
        comparison_results = {
            'total_rows_file1': 100,
            'total_rows_file2': 100,
            'matching_rows': 95,
            'only_in_file1': [],
            'only_in_file2': [],
            'differences': [
                {
                    'keys': {'id': 1},
                    'differences': {
                        'name': {'file1': 'Alice', 'file2': 'ALICE'}
                    }
                }
            ]
        }
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.html') as f:
            temp_file = f.name

        try:
            reporter = HTMLReporter()
            reporter.generate(comparison_results, temp_file)
            
            # Check file was created
            assert os.path.exists(temp_file)
            
            # Check content
            with open(temp_file, 'r') as f:
                content = f.read()
                assert 'Valdo Comparison Report' in content
                assert 'Total Rows (File 1)' in content
                assert '100' in content
        finally:
            os.unlink(temp_file)

    def test_generate_report_no_differences(self):
        """Test report generation with no differences."""
        comparison_results = {
            'total_rows_file1': 50,
            'total_rows_file2': 50,
            'matching_rows': 50,
            'only_in_file1': [],
            'only_in_file2': [],
            'differences': []
        }
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.html') as f:
            temp_file = f.name

        try:
            reporter = HTMLReporter()
            reporter.generate(comparison_results, temp_file)
            
            assert os.path.exists(temp_file)
            
            with open(temp_file, 'r') as f:
                content = f.read()
                assert 'Valdo Comparison Report' in content
        finally:
            os.unlink(temp_file)
