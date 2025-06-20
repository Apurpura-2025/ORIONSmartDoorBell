#!/bin/bash

#File to congiure
MQQT_CONF="/etc/mosquitto/mosquitto.conf"

#Backup before changing
sudo cp $MQQT_CONF $MQQT_CONF.bak

#Define listeners and config options
CONFIG="
#Smart Doorbell MQTT Configuration
#Default MQTT socket
listener 1883
allow_anonymous true

#Unsecure Web Socket
listener 9001
protocol websockets
allow_anonymous true

#secure Web Socket
listener 9002
protocol websockets
cafile /etc/mosquitto/certs/orion_ca.crt
keyfile /etc/mosquitto/certs/ring_server.key
certfile /etc/mosquitto/certs/ring_server.crt"

#Check if the file exists
if grep -q "Smart Doorbell MQTT Configuration" $MQQT_CONF; then
    echo "MQTT configuration already exists in $MQQT_CONF"
else
    # Append the configuration to the file
    echo "$CONFIG" | sudo tee -a $MQQT_CONF > /dev/null
    echo "MQTT configuration added to $MQQT_CONF"
fi

# Restart the Mosquitto service to apply changes
echo "Restarting Mosquitto service..."
sudo systemctl restart mosquitto && echo "Mosquitto service restarted successfully." || echo "Failed to restart Mosquitto service."