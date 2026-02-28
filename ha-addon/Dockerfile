# Home Assistant Addon — thin wrapper over the main Sendspin Bluetooth Bridge image.
# HA Supervisor will build this Dockerfile using the ha-addon/ directory as the
# build context.  The base image already contains all Python source and
# dependencies; we only add the HA-specific entry point.
ARG BUILD_FROM=ghcr.io/loryanstrant/sendspin-client:latest
FROM ${BUILD_FROM}

# HA Supervisor expects the entry point to be at /run.sh by convention, but any
# path is fine as long as config.yaml's `entrypoint` field (or CMD/ENTRYPOINT)
# points to it.  We place it at /ha_run.sh to avoid collisions.
COPY run.sh /ha_run.sh
RUN chmod +x /ha_run.sh

# Override the base image ENTRYPOINT so HA Supervisor starts our bootstrap.
ENTRYPOINT ["/ha_run.sh"]
