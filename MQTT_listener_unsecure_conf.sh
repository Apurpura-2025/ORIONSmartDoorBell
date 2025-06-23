#!/bin/bash

MQTT_CONF="/etc/mosquitto/mosquitto.conf"
CONF_DIR="/etc/mosquitto/conf.d"
UNSECURE_CONF="$CONF_DIR/unsecure.conf"

# Backup once
if [ ! -f "$MQTT_CONF.bak" ]; then
    sudo cp "$MQTT_CONF" "$MQTT_CONF.bak"
fi

# Ensure include_dir exists in main conf
if ! grep -q "^include_dir $CONF_DIR" "$MQTT_CONF"; then
    echo "🔧 Adding include_dir to mosquitto.conf"
    echo "include_dir $CONF_DIR" | sudo tee -a "$MQTT_CONF" > /dev/null
fi

# Create unsecure.conf if it doesn't already exist
if [ ! -f "$UNSECURE_CONF" ]; then
    echo "🔧 Creating unsecure.conf"
    sudo tee "$UNSECURE_CONF" > /dev/null <<EOF
# Smart Doorbell MQTT Unsecure Configuration
listener 1883
allow_anonymous true

listener 9001
protocol websockets
allow_anonymous true
EOF
    echo "✅ Unsecure config created at $UNSECURE_CONF"
else
    echo "ℹ️ Unsecure config already exists at $UNSECURE_CONF"
fi

# Restart service
echo "🔄 Restarting Mosquitto..."
sudo systemctl restart mosquitto && echo "✅ Mosquitto restarted." || echo "❌ Restart failed."
🔒 Final mqtt_secure_conf.sh