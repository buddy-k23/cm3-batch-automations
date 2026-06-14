# Configuration Change Management

This document describes the approval workflow for configuration file changes
in the Valdo project.

## Overview

All configuration files under `config/` (mappings, rules, suites, masking)
are protected by a two-layer review process:

1. **Automated validation** -- a CI workflow checks structure and required fields.
2. **Code-owner approval** -- designated reviewers must approve the PR before merge.

## What is protected

| Directory           | Contents                        | CODEOWNER     |
|---------------------|---------------------------------|---------------|
| `config/mappings/`  | Field-mapping JSON files        | @buddy-k23    |
| `config/rules/`     | Business-rule JSON files        | @buddy-k23    |
| `config/suites/`    | Pipeline suite YAML definitions | @buddy-k23    |
| `config/masking/`   | Data-masking configurations     | @buddy-k23    |

## How to request a config change

1. Create a feature branch from `main`.
2. Edit or add the configuration file(s).
3. Run local validation to catch errors early:
   ```bash
   python3 -c "
   from src.utils.config_validator import validate_mapping_file
   print(validate_mapping_file('config/mappings/your_file.json'))
   "
   ```
4. Open a pull request targeting `main`.
5. The **Validate Config Files** CI check runs automatically on any PR
   that touches `config/**`.  Fix any reported errors.
6. A code owner listed in `.github/CODEOWNERS` must approve the PR.
7. Once CI passes and approval is granted, the PR can be merged.

## Automated checks

The `validate-config.yml` workflow validates:

- **Mapping files** -- valid JSON with required keys (`mapping_name`,
  `version`, `source`, `target`, `fields`) and at least one field entry
  containing `name`, `data_type`, and `target_name`.
- **Rules files** -- valid JSON with `metadata` (containing `name` and
  `description`) and a `rules` array where each rule has `id`, `name`,
  `type`, and `severity`.
- **Suite files** -- valid YAML with `name` and `steps`, where each step
  contains `name`, `type`, `file_pattern`, and `mapping`.

Validation errors appear as inline annotations on the PR diff.

## Audit trail

Every merged configuration change is tracked by Git:

- **Commit SHA** uniquely identifies the exact change.
- **PR number** links to the review discussion and approval.
- **CODEOWNERS** ensures the designated reviewer approved the change.

To find who last changed a config file and when:

```bash
git log --oneline -5 -- config/mappings/customer_batch_universal.json
```

## Adding new config owners

Edit `.github/CODEOWNERS` and add the GitHub username(s) for the relevant
directory pattern.  See the
[GitHub CODEOWNERS documentation](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
for syntax details.
