# GitLab CI/CD Guide

## Overview

This project uses GitLab CI/CD for automated testing, building, and deployment.

## Pipeline Stages

### 1. Lint Stage

**Jobs:**
- `lint:flake8` - Code linting with flake8
- `lint:black` - Code formatting check with black

**Purpose:**
- Ensure code quality
- Enforce coding standards
- Catch common errors early

### 2. Test Stage

**Jobs:**
- `test:unit` - Run unit tests with coverage
- `test:integration` - Run integration tests
- `test:api` - Run API endpoint tests (new!)

**Purpose:**
- Validate functionality
- Ensure code coverage (minimum 80%)
- Test REST API endpoints
- Generate coverage reports

**Artifacts:**
- Coverage report (XML format)
- HTML coverage report
- Test results
- API test results

### 3. Build Stage

**Jobs:**
- `build:pex` - Build PEX executable
- `build:rpm` - Build RPM package
- `build:api-docker` - Build API Docker image (optional)

**Purpose:**
- Create deployable artifacts
- Package application for distribution
- Build API container image

**Artifacts:**
- `dist/valdo.pex` - PEX executable (30 days)
- RPM package (30 days)
- Docker image (registry)

**Runs on:**
- `main` branch
- Git tags

### 4. Deploy Stage

**Jobs:**
- `deploy:staging` - Deploy to staging (manual)
- `deploy:staging-api` - Deploy API to staging (manual, new!)
- `deploy:production` - Deploy to production (manual, tags only)
- `deploy:production-api` - Deploy API to production (manual, tags only, new!)

**Purpose:**
- Deploy to environments
- Deploy REST API server
- Manual approval required

## Pipeline Triggers

### Automatic Triggers

- **Push to any branch**: Runs lint and test stages
- **Push to main**: Runs all stages including build
- **Create tag**: Runs all stages including production deployment
- **Merge request**: Runs lint and test stages

### Manual Triggers

- Deployment jobs require manual approval
- Can be triggered from GitLab UI

## Viewing Pipeline Results

### In GitLab UI

1. Go to **CI/CD > Pipelines**
2. Click on pipeline number
3. View job status and logs
4. Download artifacts

### Coverage Reports

1. Go to **CI/CD > Pipelines**
2. Click on pipeline
3. Click on `test:unit` job
4. View coverage in job output
5. Download `htmlcov/` artifact for detailed report

## Local Pipeline Testing

You can test the pipeline locally before pushing:

```bash
# Install gitlab-runner (one time)
curl -L https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.rpm.sh | sudo bash
sudo yum install gitlab-runner

# Run pipeline locally
gitlab-runner exec docker lint:flake8
gitlab-runner exec docker test:unit
```

## Pipeline Configuration

### Environment Variables

Set in **Settings > CI/CD > Variables**:

**For Testing:**
- `ORACLE_USER` - Database username (protected, masked)
- `ORACLE_PASSWORD` - Database password (protected, masked)
- `ORACLE_DSN` - Database connection string (protected)

**For API:**
- `API_PORT` - API server port (default: 8000)
- `API_HOST` - API server host (default: 0.0.0.0)
- `API_WORKERS` - Number of Uvicorn workers (default: 4)

**For Deployment:**
- `STAGING_SERVER` - Staging server address
- `STAGING_API_SERVER` - Staging API server address (new!)
- `PRODUCTION_SERVER` - Production server address
- `PRODUCTION_API_SERVER` - Production API server address (new!)
- `DEPLOY_USER` - Deployment user
- `SSH_PRIVATE_KEY` - SSH key for deployment (protected, masked)

### Protected Variables

- Mark sensitive variables as **Protected**
- Mark secrets as **Masked**
- Use **Environment-specific** variables

## Customizing the Pipeline

### Skip Pipeline

Add to commit message:
```
ci: Update documentation [skip ci]
```

### Run Specific Jobs

Use `only` or `except` in job definition:

```yaml
my_job:
  script:
    - echo "Running job"
  only:
    - main
    - merge_requests
  except:
    - tags
```

### Add New Job

Edit `.gitlab-ci.yml`:

```yaml
my_new_job:
  stage: test
  image: python:3.9
  script:
    - echo "Running my job"
    - python my_script.py
  artifacts:
    paths:
      - output/
```

## Pipeline Optimization

### Caching

The pipeline caches:
- pip packages (`.cache/pip`)
- Virtual environment (`venv/`)

This speeds up subsequent runs.

### Parallel Jobs

Lint and test jobs run in parallel for faster feedback.

### Artifacts

- Coverage reports: 1 week
- Build artifacts: 30 days
- Adjust in job configuration

## Troubleshooting

### Pipeline Fails on Lint

**flake8 errors:**
```bash
# Fix locally
flake8 src/ tests/

# Auto-fix some issues
autopep8 --in-place --recursive src/ tests/
```

**black formatting:**
```bash
# Fix locally
black src/ tests/
```

### Pipeline Fails on Tests

**Check test output:**
1. Click on failed `test:unit` job
2. View logs
3. Identify failing test
4. Fix locally and push

**Run tests locally:**
```bash
pytest -v
```

### Pipeline Fails on Build

**PEX build fails:**
- Check `build_pex.sh` script
- Ensure all dependencies are in `requirements.txt`
- Test locally: `./build_pex.sh`

**RPM build fails:**
- Check `build_rpm.sh` script
- Verify spec file syntax
- Test locally on RHEL 8.9

### Coverage Below Threshold

```bash
# Check coverage locally
pytest --cov=src --cov-report=html

# View report
open htmlcov/index.html

# Add tests for uncovered code
```

## Pipeline Status Badges

Add to README.md:

```markdown
[![pipeline status](https://trgl.gitlab-dedicated.com/org/APPID-33091157-Developers/cm3-batch-automations/badges/main/pipeline.svg)](https://trgl.gitlab-dedicated.com/org/APPID-33091157-Developers/cm3-batch-automations/-/commits/main)

[![coverage report](https://trgl.gitlab-dedicated.com/org/APPID-33091157-Developers/cm3-batch-automations/badges/main/coverage.svg)](https://trgl.gitlab-dedicated.com/org/APPID-33091157-Developers/cm3-batch-automations/-/commits/main)
```

## Best Practices

1. **Always run tests locally** before pushing
2. **Fix lint errors** before committing
3. **Maintain coverage** above 80%
4. **Review pipeline logs** for warnings
5. **Use manual deployment** for production
6. **Tag releases** for version tracking
7. **Monitor pipeline performance** and optimize

## Pipeline Workflow

```
Push to branch
    ↓
Lint Stage (parallel)
    ├─ flake8
    └─ black
    ↓
Test Stage (parallel)
    ├─ unit tests (with coverage)
    └─ integration tests
    ↓
Build Stage (parallel, main/tags only)
    ├─ PEX executable
    └─ RPM package
    ↓
Deploy Stage (manual)
    ├─ staging (main branch)
    └─ production (tags only)
```

## Next Steps

1. **Push changes** to trigger pipeline
2. **Monitor pipeline** in GitLab UI
3. **Fix any failures** and push again
4. **Download artifacts** from successful builds
5. **Deploy manually** when ready

## Summary

The CI/CD pipeline:
- ✅ Automatically tests all code
- ✅ Enforces code quality standards
- ✅ Generates coverage reports
- ✅ Builds deployable artifacts
- ✅ Supports manual deployment
- ✅ Provides fast feedback

See `.gitlab-ci.yml` for complete configuration.
