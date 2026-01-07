#!/bin/bash

LAB_DIR="$(pwd)"
SHARED_DIR="$LAB_DIR/shared"

echo "=========================================="
echo "INITIALIZING SSH SUPPORT"
echo "=========================================="

echo "[1/4] Creating SSH keys directory..."
mkdir -p "$SHARED_DIR/ssh_keys"
chmod 755 "$SHARED_DIR/ssh_keys"

if [ ! -f "$SHARED_DIR/ssh_keys/id_rsa" ]; then
    echo "[2/4] Generating shared SSH key..."
    ssh-keygen -t rsa -b 2048 -f "$SHARED_DIR/ssh_keys/id_rsa" -N "" -q
    chmod 600 "$SHARED_DIR/ssh_keys/id_rsa"
    chmod 644 "$SHARED_DIR/ssh_keys/id_rsa.pub"
    echo "      ✓ SSH key generated"
else
    echo "[2/4] SSH key already exists"
fi

if [ ! -f "$SHARED_DIR/setup_ssh.sh" ]; then
    echo "[3/4] Creating setup_ssh.sh..."
    cat > "$SHARED_DIR/setup_ssh.sh" << 'EOFSCRIPT'
#!/bin/bash

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
EOFSCRIPT
    chmod +x "$SHARED_DIR/setup_ssh.sh"
    echo "      ✓ setup_ssh.sh created"
else
    echo "[3/4] setup_ssh.sh already exists"
fi

echo "[4/4] Verifying required files..."
REQUIRED_FILES=(
    "srv6_path_server.py"
    "srv6_path_client.py"
    "srv6_path_pb2.py"
    "srv6_path_pb2_grpc.py"
)

MISSING=0
for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$SHARED_DIR/$file" ]; then
        echo "      ✗ Missing: $file"
        MISSING=1
    fi
done

if [ $MISSING -eq 0 ]; then
    echo "      ✓ All required files present"
else
    echo "      ⚠ Some files are missing"
fi

echo ""
echo "=========================================="
echo "INITIALIZATION COMPLETE"
echo "=========================================="
