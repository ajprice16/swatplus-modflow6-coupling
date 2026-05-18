from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="swatplus-modflow6-coupling",
    version="0.1.0",
    author="Hydrologic Coupling Team",
    description="A Python framework for coupling SWAT+ watershed model with MODFLOW 6 groundwater model",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/ajprice16/swatplus-modflow6-coupling",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Hydrology",
    ],
    python_requires=">=3.9",
    install_requires=[
        "flopy>=3.4",
        "numpy>=1.20",
        "pandas>=1.3",
        "geopandas>=0.10",
        "shapely>=1.8",
        "rasterio>=1.2",
        "xarray>=0.20",
        "netCDF4>=1.5",
        "pydantic>=1.9",
        "pyyaml>=6.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-cov>=3.0",
            "black>=22.0",
            "flake8>=4.0",
            "mypy>=0.910",
        ],
    },
)
