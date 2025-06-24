#!/bin/bash

MQTT_CONF="/etc/mosquitto/mosquitto.conf"

# Backup once
if [ ! -f "$MQTT_CONF.bak" ]; then
    sudo cp "$MQTT_CONF" "$MQTT_CONF.bak"
fi

# Define listeners and config options
CONFIG="
# Smart Doorbell MQTT Configuration
listener 1883
allow_anonymous true

listener 9001
protocol websockets
allow_anonymous true
"

# Check if configuration already exists
if grep -q "Smart Doorbell MQTT Configuration" "$MQTT_CONF"; then
    echo "ℹ️ MQTT configuration already present in $MQTT_CONF"
else
    echo "🔧 Adding MQTT listener configuration directly to $MQTT_CONF"
    echo "$CONFIG" | sudo tee -a "$MQTT_CONF" > /dev/null
    echo "✅ Configuration added."
fi

# Restart Mosquitto service
echo "🔄 Restarting Mosquitto..."
sudo systemctl restart mosquitto && echo "✅ Mosquitto restarted." || echo "❌ Restart failed."