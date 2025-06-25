// === DOM ELEMENT REFERENCES ===
// These lines find and save parts of the webpage so we can control them with JavaScript
const camera_image = document.getElementById('camera_image');       // This is the live camera feed (video stream)
const messageDiv = document.getElementById('response');             // This is where the GPT description (AI response) will appear
const camera_button = document.getElementById('camera_control');    // This is the button the user clicks to start or stop the camera
const gpt_button = document.getElementById('gpt_control');          // This button asks the AI to describe what it sees from the camera
const listen_button = document.getElementById('listen_control');    // This button lets the user listen to the microphone at the door
const talk_button = document.getElementById('talk_control');        // This button lets the user speak through the doorbell speaker
const audio_player = document.getElementById("audioPlayer");        // This is a hidden audio player that plays sounds from the door microphone
const volume_up_button = document.getElementById("volume_up");      // This button increases the speaker volume
const volume_down_button = document.getElementById("volume_down");  // This button decreases the speaker volume

// === MQTT TOPIC CONSTANTS ===
// These are the "channels" used to send and receive messages between the webpage and the Raspberry Pi
const REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC = "ring/remote_app_control/camera";    // Used to tell the Raspberry Pi to turn the camera on or off (from the app)
const REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC = "ring/local_dev_control/camera";     // Used by the Raspberry Pi to update the app with the camera's status
const REMOTE_APP_MICROPHONE_CONTROL_TOPIC = "ring/remote_app_control/microphone";  // Used to start or stop listening through the door microphone
const REMOTE_APP_AUDIO_DATA_TOPIC = "ring/remote_app_audio_data";                  // Used to send the user's voice from the web app to the doorbell speaker
const GPT_RESPONSE_TOPIC = "ring/gptresponse";                                     // The topic where the AI (GPT) sends back its image description
const GPT_REQUEST_TOPIC = "ring/gptrequest";                                       // The topic where the app asks the AI to describe the current camera image
const LISTEN_AUDIO_RESPONSE_TOPIC = "ring/audioresponse";                          // The topic used to send door microphone audio back to the web app
const VOLUME_CONTROL_TOPIC = "ring/remote_app_control/volume";                     // Used to change the speaker volume (up or down)

// === GLOBAL VARIABLES ===
// These are shared values that the program uses throughout its operation
let is_connected = false;    // This keeps track of whether the webpage is connected to the MQTT messaging system
let mediaRecorder;           // This will be used to record audio from the user’s microphone
let audioChunks = [];        // This is where small pieces of recorded audio are stored before being sent
let cameraRetryCount = 0;    // Counts how many times we've tried to reload the camera stream (if it fails)
const MAX_RETRIES = 3;       // The maximum number of times to retry loading the camera before giving up

// === CONNECTION SECURITY CONFIG ===
// These settings help the app know how to securely connect to the MQTT server
const isSecure = location.protocol === "https:";    // This checks if the webpage is loaded using HTTPS (secure connection)
const BROKER_PORT = isSecure ? 9002 : 9001;         // If using HTTPS, use port 9002 (secure MQTT); otherwise use port 9001 (insecure MQTT)
const brokerHost = "PI's IP";                       // Replace with your Pi's IP. This should be the IP address of your Raspberry Pi (example: "192.168.1.100") 
const mqttPath = "/mqtt";                           // This is the path used by the browser to connect to the MQTT server over WebSocket

// === Emojis for fun and alerts ===
// These can be used to show messages like ,✅ Connected to MQTT broker, ❌ Failed to load MJPEG stream 
//🔌📩🔄⚠️✅📡❌📤🎤🎧

// === MQTT CLIENT INITIALIZATION ===
// This creates a new MQTT client that will connect to the Raspberry Pi
const client = new Paho.MQTT.Client(brokerHost, BROKER_PORT, mqttPath, "doorbell_" + makeid(6));

// === MQTT EVENT HANDLERS ===
// This code tells the app what to do if the connection to MQTT is lost
client.onConnectionLost = () => {
    console.warn("🔌 MQTT lost");    // Show a warning message in the browser console
    is_connected = false;            // Update the connection status to show we’re disconnected
};

// When a new message arrives through MQTT
client.onMessageArrived = (message) => {
    console.log("📩 MQTT msg from", message.destinationName);    // Log where the message came from

    // If it's a response from GPT (AI describing the image)
    if (message.destinationName === GPT_RESPONSE_TOPIC) {
        handleGPTResponseUpdate(message.payloadString);    // Show the AI's answer in the app
    // If the backend is telling the app to update the camera display (on/off)
    } else if (message.destinationName === REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC) {
        console.log("🔄 Updating camera UI from backend");
        setRemoteCameraMode(message.payloadString);        // Turn the camera button and video display on or off
     // If audio is being sent from the door microphone
    } else if (message.destinationName === LISTEN_AUDIO_RESPONSE_TOPIC) {
        handleListenFromDoorMicrophone(message);            // Play the sound on the user's device
    // If the message doesn't match any expected topic
    } else {
        console.warn("⚠️ Unhandled MQTT topic:", message.destinationName);    // Log that we got an unknown message
    }
};

// === CONNECT TO MQTT BROKER ===
// This connects the web app to the Raspberry Pi's MQTT server (message system)
client.connect({
    useSSL: isSecure,            // Use secure connection (WSS) if the webpage is loaded with HTTPS
    timeout: 5,                  // Give up if not connected within 5 seconds
    keepAliveInterval: 30,       // Send a small ping every 30 seconds to keep the connection alive
    // When the connection is successful
    onSuccess: () => {
        console.log(`✅ Connected to MQTT broker (${isSecure ? 'WSS' : 'WS'})`);
        // Subscribe (listen) to important message topics
        [GPT_RESPONSE_TOPIC, REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC, LISTEN_AUDIO_RESPONSE_TOPIC].forEach(topic => {
            client.subscribe(topic, {
                onSuccess: () => console.log("📡 Subscribed to:", topic),            // Show success message
                onFailure: err => console.error("❌ Subscribe failed:", topic, err)  // Show error if it fails
            });
        });
        is_connected = true;       // Mark that we are successfully connected
        disableControls(false);    // Re-enable the buttons in the app
    },
     // If the connection fails...
    onFailure: (err) => {
        console.error("❌ MQTT connect failed:", err.errorMessage);    // Show error in console
        showAlert("MQTT Failure", err.errorMessage);                   // Show an alert on the page
    }
});

// === AUDIO RECORDING ===
// This code sets up the user's microphone so they can send voice messages through the doorbell

// Check if the site is secure (HTTPS) and the browser supports microphone access
if (location.protocol === "https:" && navigator.mediaDevices?.getUserMedia) {
    // Ask the browser for permission to use the microphone
    navigator.mediaDevices.getUserMedia({
        audio: {
            echoCancellation: true,    // Reduce echo
            noiseSuppression: true,    // Filter out background noise
            autoGainControl: true      // Automatically adjust volume levels
        }
    }).then(stream => {
        // If permission is granted, create a recorder for the microphone audio
        mediaRecorder = new MediaRecorder(stream, {
            mimeType: 'audio/webm;codecs=opus',       // Format for sending over the web
            audioBitsPerSecond: 128000                // Quality setting for audio bitrate
        });

        // Save audio chunks as they're recorded
        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) audioChunks.push(event.data);   
        };

        // When recording stops, combine and send the audio
        mediaRecorder.onstop = () => {
            const blob = new Blob(audioChunks, { type: 'audio/wav' }); // Combine chunks into one audio file
            audioChunks = [];                    // Clear the buffer for next time
            // Convert the audio blob to a byte array and send it through MQTT
            const reader = new FileReader();
            reader.onload = function () {
                const uint8Array = new Uint8Array(this.result);
                SendCommand(REMOTE_APP_AUDIO_DATA_TOPIC, uint8Array);    // Send audio to the doorbell
                console.log("📤 Sent audio chunk:", uint8Array.length);
            };
            reader.readAsArrayBuffer(blob);
        };
    }).catch(err => {    // If something goes wrong (like no permission), show an alert
        alert("🎤 Microphone access error: " + err);
    });
} else {    // If the site is not using HTTPS, or the browser doesn't support mic access, show a warning
    console.warn("⚠️ Microphone not initialized. Please run the site over HTTPS to enable audio recording.");
}

// === BUTTON EVENTS ===
// This code controls what happens when the user clicks the "Talk" button

talk_button.addEventListener('click', () => {
    // If the button currently says "Talk", start recording audio
    if (talk_button.innerText === "Talk") {
        talk_button.innerText = "Stop Talking";    // Change the button label
        mediaRecorder?.start();                    // Start recording the user's voice
    } else {
        // If the button says "Stop Talking", stop recording
        talk_button.innerText = "Talk";           // Change the button label back
        mediaRecorder?.stop();                    // Stop the recording and send the audio
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
