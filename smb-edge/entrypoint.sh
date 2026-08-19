#!/bin/sh
set -eu
: "${UPM_SMB_HOSTNAME:=UPM-SITE}"
: "${UPM_SMB_INTERFACES:=lo eth0}"
for group in read_only technician operator manager administrator; do addgroup -S "upm_$group" 2>/dev/null || true; done
mkdir -p /shares/presentations /shares/incoming /shares/trash /var/lib/samba/private
sed -e "s/\${UPM_SMB_INTERFACES}/${UPM_SMB_INTERFACES}/g" \
    -e "s/\${UPM_SMB_HOSTNAME}/${UPM_SMB_HOSTNAME}/g" \
    /etc/samba/smb.conf.template >/etc/samba/smb.conf
smbd --foreground --no-process-group &
exec uvicorn upm_smb_edge.api:app --host 0.0.0.0 --port 8080
