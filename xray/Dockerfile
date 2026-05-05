# teddysun/xray ships the prebuilt xray binary + geoip/geosite,
# pulled via Timeweb's Russia-friendly Docker Hub mirror because the
# build node can't reach github.com or the cn/com gh-proxies for the release zip.
FROM dockerhub.timeweb.cloud/teddysun/xray:latest

RUN apk add --no-cache gettext

COPY config.template.json /etc/xray/config.template.json
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 1080
ENTRYPOINT ["/entrypoint.sh"]
