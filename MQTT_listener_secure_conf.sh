#!/bin/bash

MQTT_CONF="/etc/mosquitto/mosquitto.conf"
CONF_DIR="/etc/mosquitto/conf.d"
SECURE_CONF="$CONF_DIR/secure.conf"

# Backup once
if [ ! -f "$MQTT_CONF.bak" ]; then
    sudo cp "$MQTT_CONF" "$MQTT_CONF.bak"
fi

# Ensure include_dir exists
if ! grep -q "^include_dir $CONF_DIR" "$MQTT_CONF"; then
    echo "🔧 Adding include_dir to mosquitto.conf"
    echo "include_dir $CONF_DIR" | sudo tee -a "$MQTT_CONF" > /dev/null
fi

# Create secure.conf if not already created
if [ ! -f "$SECURE_CONF" ]; then
    echo "🔧 Creating secure.conf"
    sudo tee "$SECURE_CONF" > /dev/null <<EOF
# Smart Doorbell MQTT Secure Configuration
listener 9002
protocol websockets
cafile /etc/mosquitto/certs/orion_ca.crt
keyfile /etc/mosquitto/certs/ring_server.key
certfile /etc/mosquitto/certs/ring_server.crt
EOF
    echo "✅ Secure config created at $SECURE_CONF"
else
    echo "ℹ️ Secure config already exists at $SECURE_CONF"
fi

# Restart service
echo "🔄 Restarting Mosquitto..."
sudo systemctl restart mosquitto && echo "✅ Mosquitto restarted." || echo "❌ Restart failed."
