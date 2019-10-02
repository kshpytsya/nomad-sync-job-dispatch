from setuptools import find_packages
from setuptools import setup

setup(
    name="nomad-sync-job-dispatch",
    description="",
    long_description=open("README.md").read(),  # no "with..." will do for setup.py
    long_description_content_type="text/markdown; charset=UTF-8; variant=GFM",
    license="MIT",
    author="Kyrylo Shpytsya",
    author_email="kshpitsa@gmail.com",
    url="https://github.com/kshpytsya/nomad-sync-job-dispatch",
    setup_requires=["setuptools_scm"],
    use_scm_version=True,
    python_requires=">=3.7, <3.8",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    entry_points={
        "console_scripts": ["nomad-sync-job-dispatch = nomad_sync_job_dispatch._cli:main"],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        # "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Intended Audience :: Information Technology",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: MIT License",
        "Operating System :: MacOS :: MacOS X",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: POSIX",
        "Programming Language :: Python :: 3.7",
        "Topic :: System :: Systems Administration",
    ],
    install_requires=[
        "click>=7.0,<8",
        "click-log>=0.3.2,<1",
        "python-nomad>=1.1.0,<2",
    ],
)
