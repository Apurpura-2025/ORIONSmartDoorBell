#!/bin/bash

echo "Setting up Orion Doorbell..."

#update package list
sudo apt update && sudo apt upgrade -y

# Install required packages
echo "Installing required packages..."
sudo apt intall -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-pygame \
    python3-opencv \
    python3-picamera2 \
    python3-gpiozero \
    libatlas-base-dev \
    libportaudio2 \
    portaudio19-dev \
    pulseaudio \
    sox \
    alsa-utils \
    mosquitto \
    mosquitto-clients \
    ffmpeg \
    curl \
    git \
    raspi-config \

# Enable VNC
echo "Enabling VNC..."
sudo raspi-config nonint do_vnc 0

# Enable I2C
echo "Enabling I2C..."
sudo raspi-config nonint do_i2c 0

# Enable screen blanking (0 = enable, 1 = disable)
echo "Enabling screen blanking..."
sudo raspi-config nonint do_blanking 0

#Set Bluetooth speaker as default audio output
echo "Setting Bluetooth speaker as default audio output..."
#Search for a connected Bluetooth sink
BT_SINK=$(pactl list short sinks | grep bluez_output | awk '{print $2}' | head -n 1)

if [ -z "$BT_SINK" ]; then
    echo "No Bluetooth sink found. Make sure the speaker is connected."
    exit 1
fi

echo "Found Bluetooth sink: $BT_SINK"

#Set as default
pactl set-default-sink "$BT_SINK"
if [ $? -eq 0 ]; then
    echo "Default audio sink set to $BT_SINK"
else
    echo "Failed to set default sink"
    exit 1
fi

#Move any existing streams to the new default
for input in $(pactl list short sink-inputs | awk '{print $1}'); do
    pactl move-sink-input "$input" "$BT_SINK"
    echo "Moved sink input $input to $BT_SINK"
done

#Set Bluetooth speaker to 10% volume
pactl set-sink-volume "$BT_SINK" 10%
if [ $? -eq 0 ]; then
    echo "Set Bluetooth speaker volume to 10%"
else
    echo "Failed to set Bluetooth speaker volume"
    exit 1
fi

echo "Setup complete. Please reboot to apply all changes."