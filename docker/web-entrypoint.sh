#!/bin/sh
# Render the nginx config, injecting the API bearer token when configured.
#
# The bundled UI talks to the API through this nginx proxy, so when
# API_AUTH_TOKEN is set the proxy must present it. The token only ever live
# inside the container; the browser never sees it. When the token is empty the
# placeholder collapses to nothing and the proxy is unchanged.
set -eu

if [ -n "${API_AUTH_TOKEN:-}" ]; then
    AUTH_LINE="proxy_set_header Authorization \"Bearer ${API_AUTH_TOKEN}\";"
else
    AUTH_LINE=""
fi
export AUTH_LINE

# Only AUTH_LINE is substituted; nginx's own $host/$remote_addr stay intact.
# This runs as one of the nginx image's /docker-entrypoint.d/ hooks, so it must
# simply exit 0 afterwards (nginx itself is started by the image entrypoint).
envsubst '${AUTH_LINE}' \
    < /etc/nginx/default.conf.template \
    > /etc/nginx/conf.d/default.conf
