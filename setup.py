from setuptools import setup

with open("requirements.txt") as f:
    requirements = f.read().splitlines()

setup(
    name="foundry-storage-layout-inspector",
    version="0.1.0",
    py_modules=["layout_check"],
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "layout-check=layout_check:app",
        ],
    },
)