import mimetypes
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Response, status
from fastapi_cache import Coder, FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache
from pytest import fixture

from .data import get_channel_repodata, load_raw_data_file


class RepodataFilename(str, Enum):
    repodata = "repodata.json"
    current_repodata = "current_repodata.json"


class ChannelServer:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

    def get_base_url(self, add_pip=False):
        base_url = f"http://{self.host}:{self.port}"
        return f"{base_url}/pip" if add_pip else base_url

    def get_channel_url(self, channel, add_pip=False):
        return f"{self.get_base_url(add_pip)}/{channel}"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")
    yield


class NullCoder(Coder):
    @classmethod
    def encode(cls, value: Any) -> bytes:
        return value

    @classmethod
    def decode(cls, value: bytes) -> Any:
        return value


@fixture(scope="session")
def channel_server(host="localhost", port=8080):
    app = FastAPI(lifespan=lifespan)

    @app.get("/conda_format_repo/{full_path:path}")
    @cache(coder=NullCoder)
    async def conda_format_repo_contents(full_path: str):
        path = Path(f"conda_format_repo/{full_path}")
        data = load_raw_data_file(path)
        mimetype = mimetypes.guess_type(path)[0]
        return Response(data, media_type=mimetype)

    @app.get("/pip/{channel_name}/{subdir}/{filename}")
    @cache()
    async def pip_injected_repodata(
        channel_name: str,
        subdir: str,
        filename: str,
    ):
        # Serve the same repodata with 'pip' appended to the depends of every
        # python 2.x/3.x record, mirroring conda's SubdirData injection under
        # add_pip_as_python_dependency. Upstream's test fixtures bake pip into
        # the served index data this way, and serving it here makes the
        # injection visible to solvers that read repodata directly, such as
        # rattler, instead of relying on conda's SubdirData at solve time.
        try:
            validated = RepodataFilename(filename)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return get_channel_repodata(channel_name, subdir, validated.value, add_pip=True)

    @app.get("/{channel_name}/{subdir}/{filename}")
    @cache()
    async def repodata(
        channel_name: str,
        subdir: str,
        filename: str,
    ):
        # Validate the filename manually and return 404 for unsupported repodata
        # variants rather than relying on FastAPI's enum validation (which returns
        # 422). This server only serves repodata.json and current_repodata.json.
        # Compressed and sharded repodata variants, such as repodata.json.zst or
        # repodata_shards.msgpack.zst are not available, and a 404 tells libmamba
        # to fall back to repodata.json.
        try:
            validated = RepodataFilename(filename)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return get_channel_repodata(channel_name, subdir, validated.value)

    @app.get("/{full_path:path}")
    async def catch_all():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={
            "host": host,
            "port": port,
        },
        daemon=True,
    )
    thread.start()

    yield ChannelServer(host, port)
