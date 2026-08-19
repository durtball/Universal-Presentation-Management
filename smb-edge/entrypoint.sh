#!/bin/sh
set -eu
: "${UPM_SMB_HOSTNAME:=UPM-SITE}"
: "${UPM_SMB_INTERFACES:=lo eth0}"
for group in read_only technician operator manager administrator; do addgroup -S "upm_$group" 2>/dev/null || true; done
mkdir -p /shares/presentations /shares/incoming /shares/trash /var/lib/samba/private
# Presentations is an intentionally read-only Media Storage-owned mount. Validate it through the
# health endpoint, but never mutate it here. Repair only the writable share roots; setgid makes new
# directories inherit correctly without recursively changing persistent ownership/history.
chgrp upm_operator /shares/incoming
chmod 2775 /shares/incoming
chgrp upm_administrator /shares/trash
chmod 2770 /shares/trash
# /etc/passwd and /etc/group belong to the replaceable container layer. Rebuild Unix identities
# and supplementary memberships from the non-secret role map stored beside the persistent passdb.
python -m upm_smb_edge.accounts
sed -e "s/\${UPM_SMB_INTERFACES}/${UPM_SMB_INTERFACES}/g" \
    -e "s/\${UPM_SMB_HOSTNAME}/${UPM_SMB_HOSTNAME}/g" \
    /etc/samba/smb.conf.template >/etc/samba/smb.conf
smbd --foreground --no-process-group &
exec uvicorn upm_smb_edge.api:app --host 0.0.0.0 --port 8080
