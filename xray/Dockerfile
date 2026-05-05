FROM mirror.gcr.io/library/alpine:3.20

RUN apk add --no-cache ca-certificates gettext wget unzip tzdata

ARG XRAY_VERSION=v25.3.6
# Direct github.com from RU is unreliable; try a few proxies, fall back to direct.
RUN set -eu; \
    URL="XTLS/Xray-core/releases/download/${XRAY_VERSION}/Xray-linux-64.zip"; \
    for prefix in \
        "https://gh-proxy.com/https://github.com/" \
        "https://ghfast.top/https://github.com/" \
        "https://hub.gitmirror.com/https://github.com/" \
        "https://github.com/"; do \
      echo "try $prefix$URL"; \
      if wget -q --tries=2 --timeout=30 -O /tmp/xray.zip "$prefix$URL" && [ -s /tmp/xray.zip ]; then \
        echo "OK $prefix"; break; \
      fi; \
      rm -f /tmp/xray.zip; \
    done; \
    test -s /tmp/xray.zip; \
    unzip -j /tmp/xray.zip xray geoip.dat geosite.dat -d /usr/local/bin/; \
    chmod +x /usr/local/bin/xray; \
    rm /tmp/xray.zip

COPY config.template.json /etc/xray/config.template.json
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 1080
ENTRYPOINT ["/entrypoint.sh"]
