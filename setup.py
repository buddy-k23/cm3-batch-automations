"""Setup configuration for Valdo."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="valdo",
    version="0.1.0",
    author="Development Team",
    description="Automated file parsing, validation, and comparison tool for batch processing",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(exclude=["tests", "tests.*"]),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
    ],
    # S14-2 (#417): one canonical runtime — 3.11 across RPM (python3.11
    # AppStream), Docker (python:3.11-slim), and the .venv311 dev venv.
    python_requires=">=3.11",
    package_data={
        "src.reports": ["static/*.js"],
    },
    install_requires=requirements,
    extras_require={
        "dev": [
            "black>=22.0.0",
            "flake8>=5.0.0",
            "mypy>=0.990",
            "pylint>=2.15.0",
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "valdo=src.main:main",
        ],
    },
)
