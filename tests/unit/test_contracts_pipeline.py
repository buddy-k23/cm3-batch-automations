from src.contracts.pipeline_profile import PipelineProfile
from src.contracts.regression_workflow import RegressionWorkflowContract


def test_pipeline_profile_contract_validates_minimum_shape():
    model = PipelineProfile.model_validate({
        "source_system": "SRC_A",
        "stages": {"ingest": {"enabled": True, "command": "echo ok"}},
    })
    assert model.source_system == "SRC_A"
    assert model.stages["ingest"].enabled is True


def test_regression_workflow_contract_defaults_and_validation():
    model = RegressionWorkflowContract.model_validate({
        "name": "wf",
        "parse": {"enabled": True, "input_file": "a.txt", "mapping": "m.json"},
        "validate": {"enabled": True, "input_file": "b.txt", "mapping": "m.json"},
    })
    assert model.name == "wf"
    assert model.parse.enabled is True
    assert model.validate_stage.enabled is True
    assert model.gates.fail_fast is True
