#!/bin/bash
set -e  # Exit on error

# === CONFIGURAZIONE ===
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CERTS_DIR="${PROJECT_DIR}/certs"
SHARED_DIR="${PROJECT_DIR}/shared"

CA_DAYS=3650
CERT_DAYS=365
SERVER_CN="ctrl"

echo "=========================================="
echo "  Setup gRPC Secure (Certs & Protos)"
echo "=========================================="
echo ""
echo "Project Dir: $PROJECT_DIR"
echo "Certs Dir:   $CERTS_DIR"
echo "Shared Dir:  $SHARED_DIR"
echo ""

# === CREAZIONE STRUTTURE CARTELLE ===
echo ""
echo "[1/5] Creazione strutture..."
mkdir -p "$CERTS_DIR"
mkdir -p "$SHARED_DIR"

cd "$CERTS_DIR"

# === FILE CONFIG OPENSSL PER SERVER ===
cat > server_openssl.cnf <<EOF
[ req ]
default_bits       = 2048
distinguished_name = dn
x509_extensions    = v3
prompt             = no

[ dn ]
CN = $SERVER_CN
O  = Kathara Lab
OU = Controller

[ v3 ]
subjectAltName = @alt_names
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[ alt_names ]
DNS.1 = $SERVER_CN
DNS.2 = localhost
IP.1 = 127.0.0.1
EOF

echo "✓ File configurazione OpenSSL creato"

# === GENERA CA (Certificate Authority) ===
echo ""
echo "[2/5] Certificati CA..."

if [ ! -f ca.key ]; then
    echo "  Generazione nuova CA..."
    openssl genrsa -out ca.key 4096
    
    openssl req -x509 -new -nodes -key ca.key -sha256 -days $CA_DAYS \
        -out ca.crt \
        -subj "/C=IT/ST=Piedmont/L=Turin/O=Kathara-Lab/OU=Research/CN=Kathara-CA"
    
    echo "✓ CA generata: ca.key, ca.crt"
else
    echo "✓ CA già esistente, riuso"
fi

# === GENERA CERTIFICATO SERVER (Controller) ===
echo ""
echo "[3/5] Certificato Server (Controller)..."

if [ -f server.key ] && [ -f server.crt ]; then
    echo "  Certificati server già esistenti"
    read -p "  Rigenerare? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "✓ Mantengo certificati esistenti"
    else
        rm -f server.key server.crt server.csr
        REGEN=1
    fi
else
    REGEN=1
fi

if [ "${REGEN:-0}" -eq 1 ]; then
    echo "  Generazione certificato server..."
    
    openssl genrsa -out server.key 4096
    
    openssl req -new -key server.key -out server.csr \
        -config server_openssl.cnf
    
    openssl x509 -req -in server.csr \
        -CA ca.crt -CAkey ca.key -CAcreateserial \
        -out server.crt -days $CERT_DAYS -sha256 \
        -extfile server_openssl.cnf -extensions v3
    
    rm -f server.csr
    
    echo "✓ Certificato server generato"
fi

# === GENERA CERTIFICATO CLIENT GENERICO ===
echo ""
echo "[3.5/5] Certificato Client generico..."

if [ ! -f client.key ]; then
    echo "  Generazione certificato client generico..."
    
    openssl genrsa -out client.key 4096
    
    openssl req -new -key client.key -out client.csr \
        -subj "/C=IT/ST=Piedmont/L=Turin/O=Kathara-Lab/OU=AS-Nodes/CN=as-client"
    
    openssl x509 -req -in client.csr \
        -CA ca.crt -CAkey ca.key -CAcreateserial \
        -out client.crt -days $CERT_DAYS -sha256
    
    rm -f client.csr
    
    echo "✓ Certificato client generico creato"
else
    echo "✓ Certificato client già esistente"
fi

# === GENERA CERTIFICATI PER OGNI AS (opzionale) ===
echo ""
echo "[3.6/5] Certificati per AS specifici (opzionale)..."

for as in as1 as3 as4 as5 as6; do
    if [ ! -f "${as}.key" ]; then
        openssl genrsa -out "${as}.key" 4096
        
        openssl req -new -key "${as}.key" -out "${as}.csr" \
            -subj "/C=IT/ST=Piedmont/L=Turin/O=Kathara-Lab/OU=AS-Nodes/CN=${as}"
        
        openssl x509 -req -in "${as}.csr" \
            -CA ca.crt -CAkey ca.key -CAcreateserial \
            -out "${as}.crt" -days $CERT_DAYS -sha256
        
        rm -f "${as}.csr"
        
        echo "  ✓ ${as}: certificato generato"
    else
        echo "  ✓ ${as}: già esistente"
    fi
done

# === GENERA FILE gRPC DA PROTO ===
echo ""
echo "[4/5] Generazione file gRPC dai .proto..."

cd "$PROJECT_DIR"

# Lista dei file proto da processare
PROTO_FILES=(
    "nodeinfo.proto"
    "bgp_segments.proto"
    "srv6_path.proto"
)

for proto in "${PROTO_FILES[@]}"; do
    if [ -f "$proto" ]; then
        echo "  Processando $proto..."
        
        python3 -m grpc_tools.protoc \
            -I. \
            --python_out="$SHARED_DIR" \
            --grpc_python_out="$SHARED_DIR" \
            "$proto"
        
        # Genera il nome base del file
        base_name="${proto%.proto}"
        
        if [ -f "$SHARED_DIR/${base_name}_pb2.py" ]; then
            echo "    ✓ ${base_name}_pb2.py"
            echo "    ✓ ${base_name}_pb2_grpc.py"
        else
            echo "    ✗ Errore generazione ${base_name}"
        fi
    else
        echo "  $proto non trovato, skip"
    fi
done

# === COPIA CERTIFICATI IN SHARED ===
echo ""
echo "[5/5] Copia certificati in /shared..."

# Crea directory certs in shared
mkdir -p "$SHARED_DIR/certs"

# Copia tutti i certificati
cp "$CERTS_DIR/ca.crt" "$SHARED_DIR/certs/"
cp "$CERTS_DIR/ca.key" "$SHARED_DIR/certs/"
cp "$CERTS_DIR/server.key" "$SHARED_DIR/certs/"
cp "$CERTS_DIR/server.crt" "$SHARED_DIR/certs/"
cp "$CERTS_DIR/client.key" "$SHARED_DIR/certs/"
cp "$CERTS_DIR/client.crt" "$SHARED_DIR/certs/"

# Copia certificati AS se esistono
for as in as1 as2 as3 as4 as5 as6; do
    if [ -f "$CERTS_DIR/${as}.key" ]; then
        cp "$CERTS_DIR/${as}.key" "$SHARED_DIR/certs/"
        cp "$CERTS_DIR/${as}.crt" "$SHARED_DIR/certs/"
    fi
done

# Imposta permessi
chmod 600 "$SHARED_DIR/certs/"*.key
chmod 644 "$SHARED_DIR/certs/"*.crt

echo "✓ Certificati copiati in $SHARED_DIR/certs/"

# === COPIA SCRIPT PYTHON IN SHARED ===
echo ""
echo "[5.5/5] Copia script Python in /shared..."

# Lista degli script da copiare
SCRIPTS=(
    "registration_server.py"
    "registration_client.py"
    "bgp_segments_controller.py"
    "bgp_segments_as.py"
    "srv6_path_server.py"
    "srv6_path_client.py"
)

for script in "${SCRIPTS[@]}"; do
    if [ -f "$PROJECT_DIR/$script" ]; then
        cp "$PROJECT_DIR/$script" "$SHARED_DIR/"
        chmod +x "$SHARED_DIR/$script"
        echo "  ✓ $script"
    fi
done

# === VERIFICA CERTIFICATI ===
echo ""
echo "[6/6] Verifica certificati..."

echo ""
echo "CA Certificate:"
openssl x509 -in "$CERTS_DIR/ca.crt" -noout -subject -issuer -dates

echo ""
echo "Server Certificate:"
openssl x509 -in "$CERTS_DIR/server.crt" -noout -subject -dates

echo ""
echo "Verifica catena server:"
if openssl verify -CAfile "$CERTS_DIR/ca.crt" "$CERTS_DIR/server.crt"; then
    echo "✓ Certificato server valido"
else
    echo "✗ Certificato server NON valido"
fi

echo ""
echo "Verifica catena client:"
if openssl verify -CAfile "$CERTS_DIR/ca.crt" "$CERTS_DIR/client.crt"; then
    echo "✓ Certificato client valido"
else
    echo "✗ Certificato client NON valido"
fi

# === RIEPILOGO ===
echo ""
echo "=========================================="
echo "  Setup Completato!"
echo "=========================================="
echo ""
echo "Certificati generati in: $CERTS_DIR"
echo "Certificati copiati in:  $SHARED_DIR/certs"
echo "File gRPC generati in:   $SHARED_DIR"
echo ""
echo "File disponibili:"
ls -lh "$SHARED_DIR/certs/" 2>/dev/null | grep -E '\.(key|crt)$' || echo "  (vuoto)"
echo ""
echo "File gRPC generati:"
ls -1 "$SHARED_DIR/"*_pb2*.py 2>/dev/null || echo "  (nessuno)"
echo ""
echo "Utilizzo:"
echo "  Server (TLS):    python3 /shared/srv6_path_server_tls.py"
echo "  Client (TLS):    python3 /shared/srv6_path_client_tls.py --dest 65004"
echo "  Server (mTLS):   python3 /shared/srv6_path_server_tls.py --mtls"
echo "  Client (mTLS):   python3 /shared/srv6_path_client_tls.py --dest 65004 --mtls --cert as1"
echo ""
echo "=========================================="
