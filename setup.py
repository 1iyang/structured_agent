#!/usr/bin/env python3
"""Setup script for structured_agent package."""

from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="structured-agent",
    version="5.0.0",
    author="OpenAI Codex",
    description="Local state-transfer agent experiments for support and bug triage",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=requirements,
    scripts=[
        "run_support_workflow.py",
        "run_bug_triage_workflow.py",
    ],
    include_package_data=True,
    zip_safe=False,
)
