import json
from functools import lru_cache
from pathlib import Path

from importlib.resources import files


# This package contains datafiles converted from the original index*.json files with
# jq '.|to_entries|map(select((.value.subdir=="noarch" or (.value | has("noarch")))))|from_entries' index4.json >channel-4_noarch.json
# jq '.|to_entries|map(select((.value.subdir=="noarch" or (.value | has("noarch")))|not))|from_entries' index4.json >channel-4_non-noarch.json


def load_raw_data_file(filename: Path):
    with open(files(__package__).joinpath(filename), "rb") as ifh:
        data = ifh.read()
    return data


def load_data_file(filename: Path):
    with open(files(__package__).joinpath(filename)) as ifh:
        data = json.load(ifh)
    return data


SUBDIR_MAP = {
    "noarch": {},
    "emscripten-wasm32": {"arch": "wasm32", "platform": "emscripten"},
    "freebsd-64": {"arch": "x86_64", "platform": "freebsd"},
    "linux-32": {"arch": "x86", "platform": "linux"},
    "linux-64": {"arch": "x86_64", "platform": "linux"},
    "linux-aarch64": {"arch": "aarch64", "platform": "linux"},
    "linux-armv6l": {"arch": "armv6l", "platform": "linux"},
    "linux-armv7l": {"arch": "armv7l", "platform": "linux"},
    "linux-ppc64": {"arch": "ppc64", "platform": "linux"},
    "linux-ppc64le": {"arch": "ppc64le", "platform": "linux"},
    "linux-riscv64": {"arch": "riscv64", "platform": "linux"},
    "linux-s390x": {"arch": "s390x", "platform": "linux"},
    "osx-64": {"arch": "x86_64", "platform": "osx"},
    "osx-arm64": {"arch": "arm64", "platform": "osx"},
    "wasi-wasm32": {"arch": "wasm32", "platform": "wasi"},
    "win-32": {"arch": "x86", "platform": "win"},
    "win-64": {"arch": "x86_64", "platform": "win"},
    "win-arm64": {"arch": "arm64", "platform": "win"},
    "zos-z": {"arch": "z", "platform": "zos"},
}


@lru_cache(maxsize=None)
def get_channel_repodata(channel_name, subdir, filename):
    # This currently offers neither the add_pip nor the merge_noarch options
    assert filename in ("repodata.json", "current_repodata.json")
    info = {"subdir": subdir} | SUBDIR_MAP[subdir]
    source = "noarch" if subdir == "noarch" else "non-noarch"
    packages = load_data_file(Path(f"{channel_name}_{source}.json"))
    repodata = {
        "info": info,
        "packages": packages,
    }
    return repodata
