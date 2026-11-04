# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS node
RUN apt-get update && apt-get install -y --no-install-recommends binutils && strip /usr/local/bin/node
FROM python:3.13-slim-bookworm AS pack
COPY --from=node /usr/local/bin/node /tmp/node
COPY --from=node /usr/local/LICENSE /tmp/node-LICENSE
RUN python -m pip install --no-cache-dir --only-binary=:all: --no-deps --target=/tmp/media imageio-ffmpeg==0.6.0
# Keep the static decoder isolated from host glibc character-conversion plugins.
# Mixing the two can crash FFmpeg on DVB metadata in valid MPEG-TS streams.
RUN set -eu; cd /; mkdir -p runtime; \
    for path in bin lib lib64 usr/lib64 usr/lib/*-linux-gnu usr/lib/ssl usr/lib/locale usr/lib/os-release \
        etc/ssl etc/nsswitch.conf etc/passwd etc/group etc/localtime etc/os-release etc/ld.so.cache \
        usr/share/ca-certificates usr/share/zoneinfo usr/share/common-licenses usr/share/doc \
        usr/bin/cat usr/bin/sh usr/bin/dash usr/bin/env usr/bin/ls usr/bin/id \
        usr/local/bin/python3 usr/local/bin/python3.13 usr/local/lib; do \
        if [ -e "$path" ] || [ -L "$path" ]; then cp -a --parents "$path" /runtime/; fi; \
    done; \
    rm -rf /runtime/usr/lib/*-linux-gnu/gconv; \
    cd /runtime/usr/local/lib/python3.13; rm -rf ensurepip idlelib tkinter site-packages/pip*; \
    find . -name __pycache__ -type d -prune -exec rm -rf '{}' +; \
    install -m755 /tmp/node /runtime/usr/local/bin/node; \
    install -m755 /tmp/media/imageio_ffmpeg/binaries/ffmpeg-linux-* /runtime/usr/local/bin/ffmpeg; \
    install -Dm644 /tmp/node-LICENSE /runtime/usr/local/share/doc/node/LICENSE; \
    install -Dm644 /tmp/media/imageio_ffmpeg-*.dist-info/LICENSE /runtime/usr/local/share/doc/ffmpeg/LICENSE; \
    install -m644 /tmp/media/imageio_ffmpeg/binaries/README.md /runtime/usr/local/share/doc/ffmpeg/; \
    install -d -m1777 /runtime/tmp; install -d -o1000 -g1000 /runtime/app/data
FROM scratch
COPY --from=pack /runtime/ /
WORKDIR /app
COPY container-init.py app.py catalog.py grouping.py service.py eit.py eit-input.mjs playback.mjs probe.mjs resolution.mjs index.html style.css ui.js ./
COPY webplay.py hls.min.js hls-LICENSE.txt ./
COPY media-http.mjs ./
ENV PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 BIND=0.0.0.0 PORT=8787 DATA_DIR=/app/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python3","/app/container-init.py"]
VOLUME ["/app/data"]
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s CMD ["python3","-c","import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/health',timeout=3).read()"]
CMD ["python3","app.py"]
