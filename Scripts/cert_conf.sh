#!/bin/bash
set -e

#file variables
CERT_DIR="/certs"
CA_KEY="$CERT_DIR/orion_ca.key"
CA_CERT="$CERT_DIR/orion_ca.crt"
CA_SERIAL="$CERT_DIR/orion_ca.srl"
SERVER_KEY="$CERT_DIR/ring_server.key"
SERVER_CSR="$CERT_DIR/ring_server.csr"
SERVER_CERT="$CERT_DIR/ring_server.crt"
SAN_CONFIG="$CERT_DIR/san.cnf"

#Create the directory if it doesn't exist
mkdir -p $CERT_DIR

echo "Generating CA private key..."
openssl genrsa -out "$CA_KEY" 4096

echo "Generating CA certificate..."
openssl req -x509 -new -nodes -key "$CA_KEY" -sha256 -days 3650 -out "$CA_CERT" -subj "/C=US/ST=California/L=San Francisco/O=Smart Doorbell/OU=IT Department/CN=orion_ca"

echo "Generating server private key..."
openssl genrsa -out "$SERVER_KEY" 2048

echo "Creating Subject Alternative Name config..."
cat > "$SAN_CONFIG" <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
C = US
ST = State
L = City
O = ORION
CN = ring_server

[v3_req]
subjectAltName = @alt_names

[ alt_names ]
IP.1 = IP_ADDRESS
DNS.2 = raspberrypi
EOF

echo "Generating server certificate signing request..."
openssl req -new -key "$SERVER_KEY" -out "$SERVER_CSR" -config "$SAN_CONFIG"

echo "Signing server certificate with CA..."
openssl x509 -req -in "$SERVER_CSR" -CA "$CA_CERT" -CAkey "$CA_KEY" -CAcreateserial \
    -out "$SERVER_CERT" -days 365 -sha256 -extensions v3_req -extfile "$SAN_CONFIG"

echo "Certificates and keys created in $CERT_DIR:"
ls -l "$CERT_DIR"