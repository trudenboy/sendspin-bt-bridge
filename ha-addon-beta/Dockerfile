# Home Assistant Addon — thin wrapper over the main Sendspin Bluetooth Bridge image.
# The base image already contains S6 overlay, Python source, dependencies,
# and the S6 sendspin service definition.
ARG BUILD_FROM=ghcr.io/trudenboy/sendspin-bt-bridge:latest
FROM ${BUILD_FROM}

# No additional layers needed — the base image includes everything:
# - S6 overlay for process supervision
# - rootfs/etc/s6-overlay/s6-rc.d/sendspin service
# - entrypoint.sh (auto-detects HA addon mode via /data/options.json)
# - ENTRYPOINT ["/init"] (S6 manages the lifecycle)
