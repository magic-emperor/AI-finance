"""
Aegis Market Agent — Package Setup

Run: pip install -e .
This makes 'market_agent' importable from anywhere.
"""
from setuptools import setup, find_packages

setup(
    name="market_agent",
    version="2.1.0",
    packages=find_packages(),
    python_requires=">=3.9",
    description="Aegis AI Trading Agent — Multi-brain signal intelligence",
    author="Aegis Team",
    include_package_data=True,
    package_data={
        "market_agent": [
            "config/*.json",
            "config/*.yaml",
            "docker/*.yml",
            "reports/*.html",
        ],
    },
)
