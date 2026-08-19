FROM python:3.13-alpine

ARG WSDD_VERSION=0.8
ADD --checksum=sha256:a25263168006443c80ce55783af3630c2d75bdb349542f50493cf77d0ed24de2 \
    https://raw.githubusercontent.com/christgau/wsdd/v${WSDD_VERSION}/src/wsdd.py \
    /usr/local/bin/wsdd.py

ENTRYPOINT ["python3", "/usr/local/bin/wsdd.py"]
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD kill -0 1
