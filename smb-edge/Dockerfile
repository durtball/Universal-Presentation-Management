FROM python:3.13-alpine
RUN apk add --no-cache samba samba-common-tools tini && pip install --no-cache-dir fastapi==0.116.1 uvicorn==0.35.0
COPY smb-edge/python/src /app
COPY smb-edge/smb.conf /etc/samba/smb.conf.template
COPY smb-edge/entrypoint.sh /entrypoint.sh
ENV PYTHONPATH=/app
EXPOSE 445 8080
ENTRYPOINT ["/sbin/tini","--","/entrypoint.sh"]
