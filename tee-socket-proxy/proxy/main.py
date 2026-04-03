"""Entrypoint: starts Docker and dstack proxy servers on Unix sockets."""

import asyncio
import logging
import os
import signal

from aiohttp import web

from .tracker import ContainerTracker
from .audit import AuditLog
from .docker_proxy import DockerProxy
from .dstack_proxy import DstackProxy

logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
log = logging.getLogger("tee-proxy")

PROXY_DIR = os.environ.get("PROXY_SOCKET_DIR", "/var/run/proxy")
DOCKER_SOCK = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
DSTACK_SOCK = os.environ.get("DSTACK_SOCKET", "/var/run/dstack.sock")


async def start():
    os.makedirs(PROXY_DIR, exist_ok=True)

    dstack_sock = DSTACK_SOCK if os.path.exists(DSTACK_SOCK) else None
    tracker = ContainerTracker()
    audit = AuditLog(dstack_socket=dstack_sock)
    docker_proxy = DockerProxy(DOCKER_SOCK, tracker, audit)

    await docker_proxy.ensure_network()
    await docker_proxy.recover_tracked()
    log.info("Recovered %d tracked containers", len(tracker.all_ids()))

    docker_app = web.Application()
    docker_app.router.add_route("*", "/{path:.*}", docker_proxy.handle)
    docker_sock_path = os.path.join(PROXY_DIR, "docker.sock")
    if os.path.exists(docker_sock_path):
        os.unlink(docker_sock_path)
    docker_runner = web.AppRunner(docker_app)
    await docker_runner.setup()
    await web.UnixSite(docker_runner, docker_sock_path).start()
    os.chmod(docker_sock_path, 0o666)
    log.info("Docker proxy listening on %s", docker_sock_path)

    if dstack_sock:
        dstack_proxy = DstackProxy(dstack_sock)
        dstack_app = web.Application()
        dstack_app.router.add_route("*", "/{path:.*}", dstack_proxy.handle)
        dstack_sock_path = os.path.join(PROXY_DIR, "dstack.sock")
        if os.path.exists(dstack_sock_path):
            os.unlink(dstack_sock_path)
        dstack_runner = web.AppRunner(dstack_app)
        await dstack_runner.setup()
        await web.UnixSite(dstack_runner, dstack_sock_path).start()
        os.chmod(dstack_sock_path, 0o666)
        log.info("dstack proxy listening on %s", dstack_sock_path)
    else:
        log.warning("dstack socket not found — dstack proxy disabled")

    log.info("tee-socket-proxy running")

    stop = asyncio.Event()
    for sig_name in ("SIGINT", "SIGTERM"):
        asyncio.get_event_loop().add_signal_handler(getattr(signal, sig_name), stop.set)
    await stop.wait()
    log.info("Shutting down")


def main():
    asyncio.run(start())


if __name__ == "__main__":
    main()
