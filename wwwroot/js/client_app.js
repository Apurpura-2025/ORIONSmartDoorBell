// === DOM ELEMENT REFERENCES ===
const camera_image = document.getElementById('camera_image');       // Image element for MJPEG stream
const messageDiv = document.getElementById('response');             // GPT response text area
const camera_button = document.getElementById('camera_control');    // Start/Stop Camera button
const gpt_button = document.getElementById('gpt_control');          // Ask GPT button
const listen_button = document.getElementById('listen_control');    // Listen to door mic button
const talk_button = document.getElementById('talk_control');        // Talk into door speaker button
const audio_player = document.getElementById("audioPlayer");        // Audio player for listening response
const volume_up_button = document.getElementById("volume_up");      // Volume up button
const volume_down_button = document.getElementById("volume_down");    // Volume down button

// === MQTT TOPIC CONSTANTS ===
const REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC = "ring/remote_app_control/camera";
const REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC = "ring/local_dev_control/camera";
const REMOTE_APP_MICROPHONE_CONTROL_TOPIC = "ring/remote_app_control/microphone";
const REMOTE_APP_AUDIO_DATA_TOPIC = "ring/remote_app_audio_data";
const GPT_RESPONSE_TOPIC = "ring/gptresponse";
const GPT_REQUEST_TOPIC = "ring/gptrequest";
const LISTEN_AUDIO_RESPONSE_TOPIC = "ring/audioresponse";
const VOLUME_CONTROL_TOPIC = "ring/remote_app_control/volume";

// === GLOBAL VARIABLES ===
let is_connected = false;
let mediaRecorder;
let audioChunks = [];
let cameraRetryCount = 0;
const MAX_RETRIES = 3;

// === CONNECTION SECURITY CONFIG ===
const isSecure = location.protocol === "https:";
const BROKER_PORT = isSecure ? 9002 : 9001;
const brokerHost = "192.168.220.124";  // Replace with your Pi's IP
const mqttPath = "/mqtt";

//🔌📩🔄⚠️✅📡❌📤🎤🎧

// === MQTT CLIENT INITIALIZATION ===
const client = new Paho.MQTT.Client(brokerHost, BROKER_PORT, mqttPath, "doorbell_" + makeid(6));

// === MQTT EVENT HANDLERS ===
client.onConnectionLost = () => {
    console.warn("🔌 MQTT lost");
    is_connected = false;
};

client.onMessageArrived = (message) => {
    console.log("📩 MQTT msg from", message.destinationName);

    if (message.destinationName === GPT_RESPONSE_TOPIC) {
        handleGPTResponseUpdate(message.payloadString);
    } else if (message.destinationName === REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC) {
        console.log("🔄 Updating camera UI from backend");
        setRemoteCameraMode(message.payloadString);
    } else if (message.destinationName === LISTEN_AUDIO_RESPONSE_TOPIC) {
        handleListenFromDoorMicrophone(message);
    } else {
        console.warn("⚠️ Unhandled MQTT topic:", message.destinationName);
    }
};

// === CONNECT TO MQTT BROKER ===
client.connect({
    useSSL: isSecure,
    timeout: 5,
    keepAliveInterval: 30,
    onSuccess: () => {
        console.log(`✅ Connected to MQTT broker (${isSecure ? 'WSS' : 'WS'})`);
        [GPT_RESPONSE_TOPIC, REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC, LISTEN_AUDIO_RESPONSE_TOPIC].forEach(topic => {
            client.subscribe(topic, {
                onSuccess: () => console.log("📡 Subscribed to:", topic),
                onFailure: err => console.error("❌ Subscribe failed:", topic, err)
            });
        });
        is_connected = true;
        disableControls(false);
    },
    onFailure: (err) => {
        console.error("❌ MQTT connect failed:", err.errorMessage);
        showAlert("MQTT Failure", err.errorMessage);
    }
});

// === AUDIO RECORDING ===
if (location.protocol === "https:" && navigator.mediaDevices?.getUserMedia) {
    navigator.mediaDevices.getUserMedia({
        audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true
        }
    }).then(stream => {
        mediaRecorder = new MediaRecorder(stream, {
            mimeType: 'audio/webm;codecs=opus',
            audioBitsPerSecond: 128000
        });

        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) audioChunks.push(event.data);
        };

        mediaRecorder.onstop = () => {
            const blob = new Blob(audioChunks, { type: 'audio/wav' });
            audioChunks = [];
            const reader = new FileReader();
            reader.onload = function () {
                const uint8Array = new Uint8Array(this.result);
                SendCommand(REMOTE_APP_AUDIO_DATA_TOPIC, uint8Array);
                console.log("📤 Sent audio chunk:", uint8Array.length);
            };
            reader.readAsArrayBuffer(blob);
        };
    }).catch(err => {
        alert("🎤 Microphone access error: " + err);
    });
} else {
    console.warn("⚠️ Microphone not initialized. Please run the site over HTTPS to enable audio recording.");
}

// === BUTTON EVENTS ===
talk_button.addEventListener('click', () => {
    if (talk_button.innerText === "Talk") {
        talk_button.innerText = "Stop Talking";
        mediaRecorder?.start();
    } else {
        talk_button.innerText = "Talk";
        mediaRecorder?.stop();
    }
});

gpt_button.addEventListener('click', () => {
    if (camera_button.innerText === "Stop Camera") {
        SendCommand(GPT_REQUEST_TOPIC, "describe this image");
        SendCommand(REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC, "off");
        setRemoteCameraMode("off");
    } else {
        showAlert("Camera must be running", "Start camera before asking GPT.");
    }
});

listen_button.addEventListener('click', () => {
    const isListening = listen_button.innerText === "Listen";
    listen_button.innerText = isListening ? "Stop Listening" : "Listen";
    SendCommand(REMOTE_APP_MICROPHONE_CONTROL_TOPIC, isListening ? "on" : "off");

    audio_player.style.display = "none";  // Hide the audio player
    if (!isListening) {
        audio_player.pause();
        audio_player.src = "";
    }
});

camera_button.addEventListener('click', () => {
    const mode = camera_button.innerText === "Start Camera" ? "on" : "off";
    setRemoteCameraMode(mode);
    SendCommand(REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC, mode);
});

volume_up_button.addEventListener('click', () => {
    SendCommand(VOLUME_CONTROL_TOPIC, "up");
});

volume_down_button.addEventListener('click', () => {
    SendCommand(VOLUME_CONTROL_TOPIC, "down");
});

// === UI SYNC FUNCTIONS ===
function setRemoteCameraMode(mode) {
    console.log("Remote camera mode set to:", mode);
    camera_button.innerText = mode === "on" ? "Stop Camera" : "Start Camera";

    if (mode === "on") {
        camera_image.style.display = "inline";
        cameraRetryCount = 0;
        loadMJPEGStream();
    } else {
        camera_image.style.display = "none";
        camera_image.src = "";
    }
}

function loadMJPEGStream() {
    const timestamp = Date.now();
    camera_image.src = `/stream.mjpg?ts=${timestamp}`;
    camera_image.onerror = () => {
        console.error("❌ Failed to load MJPEG stream.");
        cameraRetryCount++;
        if (cameraRetryCount < MAX_RETRIES) {
            console.log("🔁 Retrying MJPEG stream...");
            setTimeout(loadMJPEGStream, 1000);
        } else {
            showAlert("Camera Error", "Unable to load video stream.");
        }
    };
}

// === AUDIO LISTEN HANDLER ===
function handleListenFromDoorMicrophone(message) {
    try {
        const blob = new Blob([message.payloadBytes], { type: 'audio/wav' });
        const audioUrl = URL.createObjectURL(blob);
        audio_player.src = audioUrl;
        audio_player.play().catch(err => {
            console.error("🎧 Audio playback failed:", err);
        });
    } catch (err) {
        console.error("❌ Failed to handle audio message:", err);
    }
}

// === GPT UI RESPONSE HANDLER ===
function handleGPTResponseUpdate(message) {
    if (message === "waiting for the AI to Answer...") {
        gpt_button.disabled = true;
        camera_button.disabled = true;
        displaySpinner(true);
    } else {
        displaySpinner(false);
        gpt_button.disabled = false;
        camera_button.disabled = false;
    }
    messageDiv.innerText = message;
}

// === UTILITY FUNCTIONS ===
function extractConnectedIP(address_bar) {
    const ip_expr = /\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/;
    const matches = address_bar.match(ip_expr);
    return matches ? matches[0] : "127.0.0.1";
}

function makeid(length) {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    return Array.from({ length }, () => chars[Math.floor(Math.random() * chars.length)]).join('');
}

function displaySpinner(show) {
    document.getElementById('spinner').style.display = show ? 'block' : 'none';
    camera_image.style.display = show ? 'none' : 'inline';
}

function showAlert(title, text) {
    Swal.fire({ title, text, icon: 'info', confirmButtonText: 'OK' });
}

function SendCommand(topic, payload) {
    if (!is_connected) {
        console.warn("⚠️ MQTT not connected. Skipping send:", topic);
        return;
    }
    const msg = new Paho.MQTT.Message(payload);
    msg.destinationName = topic;
    client.send(msg);
}

function disableControls(status) {
    camera_button.disabled = status;
    gpt_button.disabled = status;
    listen_button.disabled = status;
    talk_button.disabled = status;
}