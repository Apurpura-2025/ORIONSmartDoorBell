# === Standard Library and Third-Party Imports ===
import io, base64, sys, requests, threading, logging, socketserver
from http import server
import time, os, ssl, argparse, subprocess
from gpiozero import Button, MotionSensor
from picamera2 import Picamera2
import paho.mqtt.client as paho
from threading import Condition
import pygame, cv2, numpy as np
from dotenv import load_dotenv
import re
import audioUtils

#⚠️📸🛑❌🚫🕒✅👀🤖📩🎤🔈📡🔌🌐

# Suppress the pygame support prompt message
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'

# Load environment variables from .env file
load_dotenv()
# Retrieve the OpenAI API key from environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize global state variables
camera_on = False                          # Flag to indicate if camera is active
manual_override = False                    # Used in motion mode to prevent reactivation
manual_override_reset_time = 60            # Time (in seconds) before override resets
manual_override_reset_thread = None        # Thread that resets manual override
output = None                              # Will hold an instance of StreamingOutput
selected_output_device = None              # Stores selected audio output device
last_bell_time = 0                         # global cooldown tracker
BELL_COOLDOWN_SECONDS = 5

# === Class for MJPEG Streaming ===
class StreamingOutput:
    def __init__(self):
        self.frame = None                 # Most recent frame
        self.condition = Condition()      # Threading condition to wait/notify frame updates

    def write(self, frame):
        _, jpeg = cv2.imencode('.jpg', frame)     # Encode OpenCV frame to JPEG
        with self.condition:
            self.frame = jpeg.tobytes()           # Store JPEG bytes
            self.condition.notify_all()           # Notify all waiting threads

# === HTTP Request Handler for Web Interface ===
class StreamingHandler(server.BaseHTTPRequestHandler):
    def ReadClientApp(self, appfile, binary=False):
        with open(appfile, 'rb' if binary else 'r') as f:
            return f.read()                       # Read static file content

    def do_GET(self):
        try:
            if self.path == '/':
                self.send_response(301)
                self.send_header('Location', '/index.html')
                self.end_headers()
            elif self.path == '/index.html':
                content = self.ReadClientApp("./wwwroot/html_pages/client_ring_app.html").encode("utf-8")
                self._send_file_response(content, 'text/html')
            elif self.path == '/client_app.js':
                content = self.ReadClientApp('./wwwroot/js/client_app.js').encode("utf-8")
                self._send_file_response(content, 'application/javascript')
            elif self.path == '/client_app_styles.css':
                content = self.ReadClientApp('./wwwroot/css/client_app_styles.css').encode("utf-8")
                self._send_file_response(content, 'text/css')
            elif self.path.startswith('/stream.mjpg'):
                self._handle_stream()
            else:
                self.send_error(404)
        except Exception as e:
            logging.error(f"Handler error: {e}")

    def _send_file_response(self, content, content_type):
        self.send_response(200)
        self.send_header('Content-type', content_type)
        self.send_header('Content-Length', len(content))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(content)                     # Send file content

    def _handle_stream(self):
        print("📡 MJPEG stream requested")
        self.send_response(200)
        self.send_header('Cache-Control', 'no-cache, private')
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
        self.end_headers()
        try:
            while True:
                with output.condition:
                    output.condition.wait(timeout=1)
                    frame = output.frame
                if frame:
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            logging.warning("⚠️ MJPEG stream broken")

# === Threaded HTTP Server ===
class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

# === Camera Frame Capture Loop ===
def camera_capture_loop():
    global camera_on
    while True:
        if not camera_on:
            time.sleep(0.1)
            continue
        try:
            frame = camera.capture_array()    # Capture frame as numpy array
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)  # Convert BGR to RGB
            output.write(rgb_frame)
            time.sleep(1 / 24)               # Target 24 FPS
        except Exception as e:
            print("⚠️ Frame capture error:", e)

# === Turn Camera On or Off ===
def cameraControl(mode):
    global camera_on
    if mode == "on" and not camera_on:
        camera.configure(camera.create_video_configuration(main={"size": (640, 480)}))
        camera.start()
        camera.set_controls({
            "AwbMode": 0,
            "ColourGains": (1.5, 2)  
        })
        camera_on = True
        print("📸 Camera started")
        threading.Thread(target=camera_capture_loop, daemon=True).start()
    elif mode == "off" and camera_on:
        camera.stop()
        camera_on = False
        print("🛑 Camera stopped")

# === Start Camera from App or Motion ===
def startCamera():
    global manual_override
    if not camera_on:
        cameraControl("on")
        client.publish(REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC, "on")
        if args.mode == "motion":
            manual_override = False

# === Stop Camera and Activate Override ===
def stopCamera():
    global manual_override, manual_override_reset_thread
    if camera_on:
        cameraControl("off")
        client.publish(REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC, "off")
        print("🚫 Manual stop triggered — override active.")
        if args.mode == "motion":
            manual_override = True
            if not manual_override_reset_thread or not manual_override_reset_thread.is_alive():
                manual_override_reset_thread = threading.Thread(target=reset_manual_override, daemon=True)
                manual_override_reset_thread.start()

# === Reset Manual Override After Delay ===
def reset_manual_override():
    global manual_override
    print(f"🕒 Manual override reset in {manual_override_reset_time}s")
    time.sleep(manual_override_reset_time)
    manual_override = False
    print("✅ Manual override lifted.")

# === Motion Sensor Trigger ===
def handleMotionMode():
    global manual_override
    print("👀 Motion detected!")
    if not camera_on and not manual_override:
        startCamera()
    else:
        print("🛑 Motion ignored.")

# === Button Press Trigger ===
def handleButtonMode():
    startCamera()

# === List ALSA Audio Output Devices ===
def list_alsa_playback_devices():
    try:
        result = subprocess.run(["aplay", "-L"], capture_output=True, text=True, check=True)
        return [line.strip() for line in result.stdout.splitlines() if line and not line.startswith(" ")]
    except subprocess.CalledProcessError as e:
        print("❌ Error listing ALSA devices:", e)
        return []

# === Select a Bluetooth Audio Device if Available ===
def select_bluetooth_output_device(preferred_keywords=["bluealsa", "bluetooth", "BT"]):
    global selected_output_device
    if selected_output_device:
        return selected_output_device
    for device in list_alsa_playback_devices():
        for keyword in preferred_keywords:
            if keyword.lower() in device.lower():
                selected_output_device = device
                print(f"✅ Selected BT device: {device}")
                return device
    selected_output_device = "default"
    print("⚠️ No BT device found. Using 'default'")
    return selected_output_device

def get_bt_sink_name():
    try:
        result = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if "bluez_output" in line:
                return line.split()[1]  # return sink name
    except Exception as e:
        print("❌ Could not find Bluetooth sink:", e)
    return None

def get_current_volume_percent(sink):
    try:
        result = subprocess.run(["pactl", "list", "sinks"], capture_output=True, text=True)
        inside_sink = False
        for line in result.stdout.splitlines():
            if sink in line:
                inside_sink = True
            elif inside_sink and "Volume:" in line and "Channel" not in line:
                match = re.search(r"(\d+)%", line)
                if match:
                    return int(match.group(1))
            elif inside_sink and line.strip() == "":
                break  # end of this sink's block
    except Exception as e:
        print("❌ Could not get volume:", e)
    return None

def change_volume(direction):
    sink = get_bt_sink_name()
    if not sink:
        print("⚠️ Bluetooth sink not found.")
        return

    current = get_current_volume_percent(sink)
    if current is None:
        print("⚠️ Could not read current volume.")
        return

    new_volume = current + 5 if direction == "up" else current - 5
    new_volume = max(0, min(100, new_volume))  # clamp between 0% and 100%

    try:
        subprocess.run(["pactl", "set-sink-volume", sink, f"{new_volume}%"], check=True)
        print(f"🔊 Volume set to {new_volume}%")
    except subprocess.CalledProcessError as e:
        print(f"❌ Volume change error: {e}")
        
def handleButtonMode():
    global last_bell_time

    now = time.time()
    if now - last_bell_time < BELL_COOLDOWN_SECONDS:
        print("⏳ Bell on cooldown. Ignoring press.")
        return  # Exit early

    last_bell_time = now

    # Play bell sound using ffplay
    try:
        env = os.environ.copy()
        env["DISPLAY"] = ":0"
        env["PULSE_RUNTIME_PATH"] = f"/run/user/{os.getuid()}/pulse"
        subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "bell1.mp3"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env
        )
        print("🔔 Bell sound played with ffplay")
    except Exception as e:
        print(f"❌ Failed to play bell sound: {e}")

    # In manual mode, also turn on the camera
    if args.mode == "manual":
        startCamera()

# === Send Image to OpenAI GPT-4o and Publish Response ===
def handleGPTRequest():
    
    #Comment out this line when AI integration is enabled
    #client.publish(GPT_RESPONSE_TOPIC, payload="Awaiting AI integration...", qos=0, retain=False)
    
    ## AI intergration. Delete """ on either end to enable.
    client.publish(GPT_RESPONSE_TOPIC, payload="waiting for the AI to Answer...", qos=0, retain=False)
    try:
        buffer = io.BytesIO()
        camera.capture_file(buffer, format='jpeg')
        buffer.seek(0)
        img_b64 = base64.b64encode(buffer.read()).decode('utf-8')

        payload = {
            "model": "gpt-4o",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe the image in detail in 2-3 sentences."},
                    {"type": "image_url", "image_url": { "url": f"data:image/jpeg;base64,{img_b64}" }}
                ]
            }],
            "max_tokens": 400
        }

        headers = { "Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json" }
        response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()['choices'][0]['message']['content']
        print("🤖 GPT:", result)
        client.publish(GPT_RESPONSE_TOPIC, payload=result, qos=0, retain=False)

    except Exception as e:
        error_msg = f"❌ GPT error: {e}"
        print(error_msg)
        client.publish(GPT_RESPONSE_TOPIC, payload=error_msg, qos=0, retain=False)

# === MQTT Callback Handlers ===
def on_message(client, userdata, msg):
    topic = msg.topic
    print("📩 MQTT:", topic)
    if topic == REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC:
        cameraControl(msg.payload.decode())
    elif topic == REMOTE_APP_MICROPHONE_CONTROL_TOPIC:
        command = msg.payload.decode().lower()
        print("🎤 Microphone control:", command)
        if command == "on":
            audio_streamer.StartPlaying()
        elif command == "off":
            audio_streamer.StopPlaying()
    elif topic == GPT_REQUEST_TOPIC:
        threading.Thread(target=handleGPTRequest, daemon=True).start()
    elif topic == REMOTE_APP_AUDIO_DATA_TOPIC:
        print("🔈 Audio chunk received — converting and playing.")
        try:
            import tempfile

            # Save the received blob to a temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as raw_file:
                raw_file.write(msg.payload)
                raw_file.flush()
                raw_path = raw_file.name

            # Convert to WAV using ffmpeg
            wav_path = raw_path.replace(".webm", ".wav")
            subprocess.run(["ffmpeg", "-y", "-i", raw_path, wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Play it
            subprocess.run(["aplay", wav_path])

            # Clean up
            os.unlink(raw_path)
            os.unlink(wav_path)

        except Exception as e:
            print("❌ Audio playback failed:", e)
            
    elif topic == VOLUME_CONTROL_TOPIC:
        direction = msg.payload.decode()
        print(f"🔊 Volume change requested: {direction}")
        change_volume(direction)

def on_connect(client, userdata, flags, rc, properties=None):
    print("✅ MQTT connected:", rc)
    for t in [REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC, GPT_REQUEST_TOPIC,
              REMOTE_APP_MICROPHONE_CONTROL_TOPIC, REMOTE_APP_AUDIO_DATA_TOPIC,
              VOLUME_CONTROL_TOPIC]:
        client.subscribe(t)
    print("📡 Subscribed to all topics.")

def on_disconnect(client, userdata, flags, rc, properties=None):
    print("🔌 MQTT disconnected:", rc)
    stopCamera()

# === Main Program Execution ===
if __name__ == '__main__':
    # Define MQTT topics
    REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC = "ring/remote_app_control/camera"
    REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC = "ring/local_dev_control/camera"
    REMOTE_APP_MICROPHONE_CONTROL_TOPIC = "ring/remote_app_control/microphone"
    REMOTE_APP_AUDIO_DATA_TOPIC = "ring/remote_app_audio_data"
    GPT_REQUEST_TOPIC = "ring/gptrequest"
    GPT_RESPONSE_TOPIC = "ring/gptresponse"
    VOLUME_CONTROL_TOPIC = "ring/remote_app_control/volume"

    # Parse command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', type=str, default='manual', help='manual | motion')
    parser.add_argument('--secure', type=str, default='off')
    args = parser.parse_args()

    # Initialize camera, GPIO, audio
    pygame.mixer.init()
    camera = Picamera2()
    output = StreamingOutput()
    button = Button(2)
    pir = MotionSensor(4)

    # Setup MQTT
    client = paho.Client(transport="tcp")
    client.on_message = on_message
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.connect("127.0.0.1", 1883, 60)
    client.loop_start()

    # Setup audio playback
    audio_streamer = audioUtils.AudioPlayback()
    audio_streamer.SetMQTTClient(client, "ring/audioresponse")
    audio_streamer.SetPlayBackFrameCount(80)

    # Configure GPIO events
    if args.mode == "motion":
        pir.when_motion = handleMotionMode
    button.when_pressed = handleButtonMode

    # Start capturing frames in background
    threading.Thread(target=camera_capture_loop, daemon=True).start()

    # Create HTTP/HTTPS server
    port = 8001 if args.secure == "on" else 8000
    server_address = ('', port)
    httpd = StreamingServer(server_address, StreamingHandler)

    if args.secure == "on":
        cert_path = "./certs/ring_server.crt"
        key_path = "./certs/ring_server.key"
        if not os.path.exists(cert_path) or not os.path.exists(key_path):
            print("❌ TLS certs missing.")
            sys.exit(1)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        print(f"🌐 HTTPS server on port {port}")
    else:
        print(f"🌐 HTTP server on port {port}")

    # Run server until interrupted
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("🛑 Shutting down...")
        client.disconnect()
        client.loop_stop()
        camera.stop()