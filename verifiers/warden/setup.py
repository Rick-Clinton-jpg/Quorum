from pathlib import Path

from setuptools import setup, find_packages

long_description = (Path(__file__).parent / "README.md").read_text(encoding="utf-8")

setup(
    name="warden-agent",
    version="0.1.1",
    description="Lightweight Agent Drift Detection & Audit System",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Rick Clinton",
    packages=find_packages(exclude=("tests", "tests.*")),
    install_requires=["click>=8.1.0"],
    extras_require={
        "dev": ["pytest>=7.4.0"],
    },
    entry_points={
        "console_scripts": [
            "warden=warden.cli:main",
        ],
    },
    python_requires=">=3.10",
)