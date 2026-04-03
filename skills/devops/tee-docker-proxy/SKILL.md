---
name: tee-docker-proxy
description: Run and manage Docker containers inside the TEE via the socket proxy. Covers what works, what is blocked, and how to inspect the attestation audit trail.
triggers:
  - docker run
  - run a container
  - tee docker
  - container management
  - attestation audit
  - tee proxy
---

# TEE Docker Proxy

You are running inside a Trusted Execution Environment (TEE). Docker access goes through a security proxy at `DOCKER_HOST=unix:///var/run/proxy/docker.sock`. The docker CLI is available and works normally for most operations.

## What works

```bash
docker pull <image>          # pull any public image
docker run --rm <image> cmd  # run a container, get stdout/stderr
docker ps                    # list YOUR containers (proxy-managed only)
docker logs <id>             # view container logs
docker stop/kill <id>        # stop containers you created
docker rm <id>               # remove containers you created
docker images                # list available images
docker inspect <id>          # inspect your containers
```

All containers you create are automatically:
- Placed on the `hermes-attested` bridge network
- Labeled `tee-proxy.managed=true`
- Recorded in the RTMR attestation audit log

## What is blocked

| Operation | Reason |
|-----------|--------|
| `docker exec` | Isolation boundary -- use `docker run` instead |
| `docker cp` / archive | Isolation boundary |
| Accessing host containers | `docker ps` only shows proxy-managed containers |

If you need to run commands in an existing container, start a new container with a shared volume instead of exec.

## Attestation audit trail

Every container lifecycle event (create, start, stop, remove) is logged and extended into the TEE RTMR measurement. To view the audit log:

```bash
curl -s --unix-socket /var/run/proxy/docker.sock http://localhost/tee-proxy/audit | python3 -m json.tool
```

Each entry contains: timestamp, action, container_id, image, image_digest.

The audit proves which container images ran inside the TEE, in what order, with their exact digests. This is verifiable via remote attestation.

## Network

All proxy-managed containers share the `hermes-attested` bridge network. They can reach each other by container name or IP. They cannot reach the host containers (hermes, ssh-sidecar, proxy itself).

## Tips

- Use `--rm` to auto-cleanup containers after they exit
- For long-running services, use `docker run -d` and check with `docker ps`/`docker logs`
- Images are cached after first pull
- If something returns 403, it is intentionally blocked by the proxy for TEE security
