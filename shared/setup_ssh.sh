#!/bin/bash
#girare una volta per configurare ssh

apt-get update -qq > /dev/null 2>&1
apt-get install -y openssh-server openssh-client > /dev/null 2>&1

mkdir -p /root/.ssh
chmod 700 /root/.ssh

SHARED_KEY="/shared/ssh_keys/id_rsa"
SHARED_PUB="/shared/ssh_keys/id_rsa.pub"

cp "$SHARED_KEY" /root/.ssh/id_rsa
chmod 600 /root/.ssh/id_rsa

cat "$SHARED_PUB" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

cat > /etc/ssh/sshd_config << 'EOF'
Port 22
PermitRootLogin yes
PubkeyAuthentication yes
PasswordAuthentication no
ChallengeResponseAuthentication no
UsePAM yes
X11Forwarding no
PrintMotd no
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
StrictModes no
EOF

cat > /root/.ssh/config << 'EOF'
Host *
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
EOF

chmod 600 /root/.ssh/config

service ssh restart > /dev/null 2>&1

echo "[SSH] Ready"
