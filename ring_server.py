# === Importing Useful Tools ===

# These tools come with Python or are installed separately.
# They help with different tasks like internet requests, controlling hardware, and playing sound.

import io, base64, sys, requests, threading, logging, socketserver  # Basic tools for input/output, networking, logging, etc.
from http import server               # Allows this program to act like a small website
import time, os, ssl, argparse, subprocess  # Tools for working with time, files, security, command-line arguments, and running other programs
from gpiozero import Button, MotionSensor   # For using buttons and motion sensors connected to the Raspberry Pi
from picamera2 import Picamera2             # Used to control the Raspberry Pi camera
import paho.mqtt.client as paho             # For sending messages over the internet or local network (used for communication between devices)
from threading import Condition             # Used to safely share data between parts of the program that run at the same time
import pygame, cv2, numpy as np             # For playing sounds (pygame), working with images (cv2), and doing math with arrays (numpy)
from dotenv import load_dotenv              # Helps load settings from a hidden file (.env) like secret keys
import re                                   # For reading and matching patterns in text
import audioUtils                           # A custom file made for playing and recording sound

# === Emojis for fun and alerts ===
# These can be used to show messages like "✅ Success", "❌ Error", or "📡 Camera Streaming"
#⚠️📸🛑❌🚫🕒✅👀🤖📩🎤🔈📡🔌🌐

# === Prevent unnecessary messages from pygame ===
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'

# === Load secret settings from a .env file ===
# This file is hidden but contains important information, like API keys
load_dotenv()

# === Get the AI key from that hidden file ===
# This key allows the program to ask questions to an AI model like ChatGPT
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# === Set Up Some Starting Conditions ===

camera_on = False                          # Is the camera currently running? (No by default)
manual_override = False                    # Used in motion detection mode to stop the camera from turning back on automatically
manual_override_reset_time = 60            # If override is on, how long should we wait (in seconds) before turning it off?
manual_override_reset_thread = None        # This will hold a background timer to reset the override

output = None                              # Will later hold the video output that gets sent to the web app
selected_output_device = None              # Saves the name of the speaker being used
last_bell_time = 0                         # When was the last time the doorbell was pressed?
BELL_COOLDOWN_SECONDS = 5                  # How many seconds must pass before the bell can ring again
# === Class for MJPEG Streaming ===
# This class is used to manage and share camera frames with other parts of the program,
# especially for streaming live video over the web.

class StreamingOutput:
    def __init__(self):
        # This will store the most recent image frame (as JPEG bytes)
        self.frame = None

        # This is a special object used for thread synchronization.
        # It helps coordinate timing between when a frame is updated and when it's accessed.
        self.condition = Condition()

    def write(self, frame):
        # Convert the image frame (in OpenCV format) to JPEG format
        _, jpeg = cv2.imencode('.jpg', frame)

        # Lock the condition so no one else can access it while we're updating the frame
        with self.condition:
            # Save the newly converted JPEG frame as bytes
            self.frame = jpeg.tobytes()

            # Notify any other part of the program that might be waiting for a new frame
            self.condition.notify_all()

# === HTTP Request Handler for Web Interface ===
# This class handles incoming web requests from a browser.
# It serves the HTML, CSS, JavaScript files and also streams the camera video.

class StreamingHandler(server.BaseHTTPRequestHandler):

    # Helper method to load a file from the disk
    def ReadClientApp(self, appfile, binary=False):
        # Open the file in binary mode (for images/videos) or text mode (for HTML/JS/CSS)
        with open(appfile, 'rb' if binary else 'r') as f:
            return f.read()  # Return the content of the file

    # This method is called automatically when the browser makes a GET request (e.g., opening a web page)
    def do_GET(self):
        try:
            if self.path == '/':
                # Redirect the root URL to /index.html
                self.send_response(301)  # 301 = "Moved Permanently"
                self.send_header('Location', '/index.html')
                self.end_headers()

            elif self.path == '/index.html':
                # Serve the main webpage (HTML)
                content = self.ReadClientApp("./wwwroot/html_pages/client_ring_app.html").encode("utf-8")
                self._send_file_response(content, 'text/html')

            elif self.path == '/client_app.js':
                # Serve the JavaScript code for the web interface
                content = self.ReadClientApp('./wwwroot/js/client_app.js').encode("utf-8")
                self._send_file_response(content, 'application/javascript')

            elif self.path == '/client_app_styles.css':
                # Serve the CSS file for styling the webpage
                content = self.ReadClientApp('./wwwroot/css/client_app_styles.css').encode("utf-8")
                self._send_file_response(content, 'text/css')

            elif self.path.startswith('/stream.mjpg'):
                # Handle a request to stream video from the camera
                self._handle_stream()

            else:
                # If the requested file doesn't exist, return a 404 (Not Found) error
                self.send_error(404)

        except Exception as e:
            # Log any unexpected error for debugging
            logging.error(f"Handler error: {e}")

    # Helper method to send the file content back to the browser
    def _send_file_response(self, content, content_type):
        self.send_response(200)  # 200 = OK
        self.send_header('Content-type', content_type)  # Set the type of file (HTML, CSS, etc.)
        self.send_header('Content-Length', len(content))  # How big the file is
        self.send_header('Cache-Control', 'no-cache')  # Don't store a copy; always fetch fresh
        self.end_headers()
        self.wfile.write(content)  # Send the file data to the browser

    # This method handles the video streaming using MJPEG format
    def _handle_stream(self):
        print("📡 MJPEG stream requested")  # Print a message when streaming is requested

        self.send_response(200)  # 200 = OK
        self.send_header('Cache-Control', 'no-cache, private')  # No caching
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')  # Special format for streaming
        self.end_headers()

        try:
            while True:
                # Wait for a new frame to be available from the camera
                with output.condition:
                    output.condition.wait(timeout=1)  # Wait for up to 1 second
                    frame = output.frame  # Get the latest camera frame

                if frame:
                    # Send a frame in the MJPEG format
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)  # Write the actual image bytes
                    self.wfile.write(b'\r\n')
                    self.wfile.flush()  # Force sending the data immediately

        except (BrokenPipeError, ConnectionResetError):
            # This happens when the user closes the browser or loses connection
            logging.warning("⚠️ MJPEG stream broken")

# === Threaded HTTP Server ===

# This creates a special type of web server that can handle many users at once.

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    # This class combines two things:
    # - HTTPServer: lets us host a webpage or stream
    # - ThreadingMixIn: lets the server handle multiple requests at the same time

    allow_reuse_address = True  # Allows us to quickly restart the server without waiting for the port to free up
    daemon_threads = True       # Each user/request runs in its own background thread so the main program doesn’t get stuck

    #In simple terms:
    #This class lets the Raspberry Pi run a web server that can stream video to a webpage. 
    #It uses multiple "threads" (like extra hands) so it can do many things at once—like stream, listen, and respond to users—all without slowing down.

# === Camera Frame Capture Loop ===

def camera_capture_loop():
    global camera_on  # Use the shared variable that tells us if the camera should be on

    while True:  # Run this loop forever
        if not camera_on:
            time.sleep(0.1)  # If the camera is off, wait a little and try again
            continue         # Skip the rest and go back to the top of the loop

        try:
            # Take a picture (called a "frame") from the camera
            frame = camera.capture_array()

            # The camera gives colors in BGR (Blue-Green-Red), but we need RGB (Red-Green-Blue)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # Save the frame so it can be streamed to the web browser
            output.write(rgb_frame)

            # Wait a short time so we capture about 24 frames per second (like a movie)
            time.sleep(1 / 24)

        except Exception as e:
            # If something goes wrong (like the camera disconnects), show an error message
            print("⚠️ Frame capture error:", e)

# === Turn Camera On or Off ===

def cameraControl(mode):
    global camera_on  # Use the shared variable that tells us if the camera is currently on

    if mode == "on" and not camera_on:
        # Set up the camera to record video with a resolution of 640x480 pixels
        camera.configure(camera.create_video_configuration(main={"size": (640, 480)}))

        # Start the camera
        camera.start()

        # Adjust camera settings:
        # - AwbMode: 0 means "Manual White Balance"
        # - ColourGains: adjusts how strong the red and blue colors are (1.5 red, 2 blue)
        camera.set_controls({
            "AwbMode": 0,
            "ColourGains": (1.5, 2)  
        })

        # Tell the rest of the program that the camera is now on
        camera_on = True

        print("📸 Camera started")

        # Start the function that constantly captures frames in the background
        threading.Thread(target=camera_capture_loop, daemon=True).start()

    elif mode == "off" and camera_on:
        # Stop the camera if it is currently running
        camera.stop()
        camera_on = False
        print("🛑 Camera stopped")

# === Start Camera from App or Motion Sensor ===

def startCamera():
    global manual_override  # This variable prevents the camera from turning on repeatedly in motion mode

    if not camera_on:
        # Turn on the camera
        cameraControl("on")

        # Tell the remote app that the camera is now on (sends a message over the network)
        client.publish(REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC, "on")

        # If the system is running in motion-detection mode...
        if args.mode == "motion":
            # ...allow motion to turn the camera on again in the future
            manual_override = False
# === Stop Camera and Activate Override ===

def stopCamera():
    global manual_override, manual_override_reset_thread  # Use shared variables to control motion override and background timer

    if camera_on:
        # Turn off the camera
        cameraControl("off")

        # Let the web app know that the camera has been turned off
        client.publish(REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC, "off")

        print("🚫 Manual stop triggered — override active.")

        # If the system is using motion detection mode...
        if args.mode == "motion":
            # Set override to True so motion won't turn the camera back on immediately
            manual_override = True

            # If no timer is already running to reset the override...
            if not manual_override_reset_thread or not manual_override_reset_thread.is_alive():
                # Start a background timer to reset the override later
                manual_override_reset_thread = threading.Thread(
                    target=reset_manual_override,
                    daemon=True
                )
                manual_override_reset_thread.start()
# === Reset Manual Override After Delay ===

def reset_manual_override():
    global manual_override  # Use the shared override variable

    # Tell the user how long the override will last
    print(f"🕒 Manual override reset in {manual_override_reset_time}s")

    # Wait for the specified number of seconds (like a countdown)
    time.sleep(manual_override_reset_time)

    # Turn off the override so motion detection can work again
    manual_override = False

    print("✅ Manual override lifted.")

# === Motion Sensor Trigger ===

def handleMotionMode():
    global manual_override  # Use the shared setting that can block the camera from turning on

    print("👀 Motion detected!")  # Show a message when something moves near the sensor

    # If the camera is off and no override is active...
    if not camera_on and not manual_override:
        startCamera()  # Turn on the camera
    else:
        print("🛑 Motion ignored.")  # If the camera is already on or override is active, do nothing

# === Button Press Trigger ===

def handleButtonMode():
    startCamera()  # Turn on the camera when the button is pressed

# === List ALSA Audio Output Devices ===

def list_alsa_playback_devices():
    try:
        # Run the command "aplay -L" to list audio output devices
        result = subprocess.run(["aplay", "-L"], capture_output=True, text=True, check=True)

        # Go through each line in the result and clean it up (remove empty lines and indentation)
        return [line.strip() for line in result.stdout.splitlines() if line and not line.startswith(" ")]

    except subprocess.CalledProcessError as e:
        # If something goes wrong, print an error message
        print("❌ Error listing ALSA devices:", e)
        return []

# === Select a Bluetooth Audio Device if Available ===

def select_bluetooth_output_device(preferred_keywords=["bluealsa", "bluetooth", "BT"]):
    global selected_output_device  # This stores which speaker/device to use

    # If we’ve already chosen a device before, just use that
    if selected_output_device:
        return selected_output_device

    # Go through all available audio output devices
    for device in list_alsa_playback_devices():
        # Check if the device name contains words like "bluealsa", "bluetooth", or "BT"
        for keyword in preferred_keywords:
            if keyword.lower() in device.lower():
                selected_output_device = device  # Save the found Bluetooth device
                print(f"✅ Selected BT device: {device}")  # Show which one we picked
                return device

    # If no Bluetooth device is found, use the default speaker
    selected_output_device = "default"
    print("⚠️ No BT device found. Using 'default'")
    return selected_output_device

def get_bt_sink_name():
    try:
        # Run the command "pactl list short sinks" to list audio outputs
        result = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True)

        # Go through each line of the output
        for line in result.stdout.splitlines():
            # Look for a line that contains "bluez_output", which means it's a Bluetooth speaker
            if "bluez_output" in line:
                return line.split()[1]  # Return the name of the Bluetooth sink (speaker)

    except Exception as e:
        # If something goes wrong, show an error message
        print("❌ Could not find Bluetooth sink:", e)

    # If no Bluetooth speaker was found, return nothing
    return None

def get_current_volume_percent(sink):
    try:
        # Run the command "pactl list sinks" to get detailed info about all audio outputs
        result = subprocess.run(["pactl", "list", "sinks"], capture_output=True, text=True)

        inside_sink = False  # We'll use this to track when we're reading the info for the right speaker

        # Go through each line in the output
        for line in result.stdout.splitlines():
            if sink in line:
                inside_sink = True  # We found the sink we’re interested in
            elif inside_sink and "Volume:" in line and "Channel" not in line:
                # Look for a line that shows the volume percentage
                match = re.search(r"(\d+)%", line)
                if match:
                    return int(match.group(1))  # Return the volume as a number (like 45)
            elif inside_sink and line.strip() == "":
                break  # We've reached the end of this sink's information

    except Exception as e:
        print("❌ Could not get volume:", e)

    return None  # If we didn’t find the volume, return nothing

def change_volume(direction):
    # Step 1: Find the Bluetooth speaker
    sink = get_bt_sink_name()
    if not sink:
        print("⚠️ Bluetooth sink not found.")
        return  # Stop if we can't find a speaker

    # Step 2: Check the current volume of the speaker
    current = get_current_volume_percent(sink)
    if current is None:
        print("⚠️ Could not read current volume.")
        return  # Stop if we can’t find the volume

    # Step 3: Decide the new volume
    # If the direction is "up", increase by 5%
    # If the direction is "down", decrease by 5%
    new_volume = current + 5 if direction == "up" else current - 5

    # Make sure the new volume stays between 0% and 100%
    new_volume = max(0, min(100, new_volume))

    try:
        # Step 4: Use a system command to set the new volume
        subprocess.run(["pactl", "set-sink-volume", sink, f"{new_volume}%"], check=True)
        print(f"🔊 Volume set to {new_volume}%")  # Show the new volume level

    except subprocess.CalledProcessError as e:
        # If setting the volume fails, show an error message
        print(f"❌ Volume change error: {e}")
        
def handleButtonMode():
    global last_bell_time  # Keep track of the last time the bell was pressed

    # Get the current time
    now = time.time()

    # If the button was pressed recently, don’t do anything
    if now - last_bell_time < BELL_COOLDOWN_SECONDS:
        print("⏳ Bell on cooldown. Ignoring press.")
        return  # Exit early to prevent spamming

    # Update the time the bell was last pressed
    last_bell_time = now

    # === Play the bell sound ===
    try:
        # Copy system environment settings
        env = os.environ.copy()

        # Set up the correct environment for sound playback
        env["DISPLAY"] = ":0"  # Required for display-aware applications
        env["PULSE_RUNTIME_PATH"] = f"/run/user/{os.getuid()}/pulse"  # Path to audio system

        # Use ffplay to play the bell sound (no video display, auto exit when done)
        subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "./sounds/bell1.mp3"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env
        )
        print("🔔 Bell sound played with ffplay")

    except Exception as e:
        print(f"❌ Failed to play bell sound: {e}")

    # === In manual mode, turn on the camera too ===
    if args.mode == "manual":
        startCamera()

# === Send Image to OpenAI GPT-4o and Publish Response ===

def handleGPTRequest():
    # This line is used when AI integration is turned OFF.
    # It sends a message to the app saying “Awaiting AI integration...”
    client.publish(GPT_RESPONSE_TOPIC, payload="Awaiting AI integration...", qos=0, retain=False)

    # === To ENABLE AI integration, delete the triple quotes below (""" and """) ===
    """ 
    # Tell the app we're waiting for the AI to respond
    client.publish(GPT_RESPONSE_TOPIC, payload="waiting for the AI to Answer...", qos=0, retain=False)

    try:
        # Create a temporary in-memory file
        buffer = io.BytesIO()

        # Take a photo with the camera and save it to the buffer
        camera.capture_file(buffer, format='jpeg')
        buffer.seek(0)  # Go back to the start of the buffer

        # Convert the image to base64 format (a long string that represents the image)
        img_b64 = base64.b64encode(buffer.read()).decode('utf-8')

        # Prepare a request for the GPT-4o AI model
        payload = {
            "model": "gpt-4o",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe the image in detail in 2-3 sentences."},
                    {"type": "image_url", "image_url": { "url": f"data:image/jpeg;base64,{img_b64}" }}
                ]
            }],
            "max_tokens": 400  # Limit how long the AI response can be
        }

        # Set the headers with the OpenAI API key
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

        # Send the image and question to OpenAI and wait for a response
        response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()  # Stop and raise an error if the response is not successful

        # Get the answer text from the AI's response
        result = response.json()['choices'][0]['message']['content']
        print("🤖 GPT:", result)

        # Send the AI response back to the app
        client.publish(GPT_RESPONSE_TOPIC, payload=result, qos=0, retain=False)

    except Exception as e:
        # If anything goes wrong, show the error and send a message to the app
        error_msg = f"❌ GPT error: {e}"
        print(error_msg)
        client.publish(GPT_RESPONSE_TOPIC, payload=error_msg, qos=0, retain=False)
    """
# === MQTT Callback Handlers ===

def on_message(client, userdata, msg):
    topic = msg.topic  # Get the topic (channel) this message was sent on
    print("📩 MQTT:", topic)  # Print it to the terminal

    # === 1. Camera On/Off ===
    if topic == REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC:
        # Turn the camera on or off based on the message
        cameraControl(msg.payload.decode())

    # === 2. Microphone Control ===
    elif topic == REMOTE_APP_MICROPHONE_CONTROL_TOPIC:
        # Read the message: it should say "on" or "off"
        command = msg.payload.decode().lower()
        print("🎤 Microphone control:", command)

        # Start or stop the microphone stream
        if command == "on":
            audio_streamer.StartPlaying()
        elif command == "off":
            audio_streamer.StopPlaying()

    # === 3. AI Image Description Request ===
    elif topic == GPT_REQUEST_TOPIC:
        # Start a background thread to send image to OpenAI and get a description
        threading.Thread(target=handleGPTRequest, daemon=True).start()

    # === 4. Receiving Audio from the App ===
    elif topic == REMOTE_APP_AUDIO_DATA_TOPIC:
        print("🔈 Audio chunk received — converting and playing.")
        try:
            import tempfile

            # Save the received audio data (in webm format) to a temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as raw_file:
                raw_file.write(msg.payload)
                raw_file.flush()
                raw_path = raw_file.name

            # Convert the webm file to wav format using ffmpeg
            wav_path = raw_path.replace(".webm", ".wav")
            subprocess.run(["ffmpeg", "-y", "-i", raw_path, wav_path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Play the wav file on the speaker
            subprocess.run(["aplay", wav_path])

            # Delete the temporary files
            os.unlink(raw_path)
            os.unlink(wav_path)

        except Exception as e:
            print("❌ Audio playback failed:", e)

    # === 5. Volume Control ===
    elif topic == VOLUME_CONTROL_TOPIC:
        direction = msg.payload.decode()  # Should be "up" or "down"
        print(f"🔊 Volume change requested: {direction}")
        change_volume(direction)  # Call the function to adjust the speaker volume

def on_connect(client, userdata, flags, rc, properties=None):
    # Show a message that the system has connected to the MQTT server
    print("✅ MQTT connected:", rc)

    # Go through each topic we care about and subscribe to it
    for t in [REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC,   # for turning the camera on/off
              GPT_REQUEST_TOPIC,                       # for asking the AI to describe an image
              REMOTE_APP_MICROPHONE_CONTROL_TOPIC,     # for starting/stopping the microphone
              REMOTE_APP_AUDIO_DATA_TOPIC,             # for receiving voice messages
              VOLUME_CONTROL_TOPIC]:                   # for volume up/down commands
        client.subscribe(t)                            # Subscribe (listen) to the topic

    # Confirm that all subscriptions are complete
    print("📡 Subscribed to all topics.")
def on_disconnect(client, userdata, flags, rc, properties=None):
    # Show a message that the system has lost connection to the MQTT server
    print("🔌 MQTT disconnected:", rc)

    # As a safety step, turn off the camera
    stopCamera()

# === Main Program Execution ===
if __name__ == '__main__':
    # These are the names of communication "channels" used by the app.
    # Messages sent and received over these topics control various parts of the system.

    # Topic to control the camera (on/off) from the remote app (e.g. the website)
    REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC = "ring/remote_app_control/camera"

    # Topic used by the local device (the Raspberry Pi) to update the app about camera status
    REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC = "ring/local_dev_control/camera"

    # Topic to turn the microphone on or off from the remote app
    REMOTE_APP_MICROPHONE_CONTROL_TOPIC = "ring/remote_app_control/microphone"

    # Topic used to send audio data (from user's mic to doorbell speaker)
    REMOTE_APP_AUDIO_DATA_TOPIC = "ring/remote_app_audio_data"

    # Topic used when the app asks GPT to describe what’s on the camera
    GPT_REQUEST_TOPIC = "ring/gptrequest"

    # Topic used to receive the response from GPT with the image description
    GPT_RESPONSE_TOPIC = "ring/gptresponse"

    # Topic to control speaker volume (increase or decrease)
    VOLUME_CONTROL_TOPIC = "ring/remote_app_control/volume"
    
    # === Parse command line arguments ===

    # Create an argument parser to read inputs from the command line
    parser = argparse.ArgumentParser()
    
    # Add an argument called '--mode'
    # This lets the user choose between two modes:
    #   - "manual": the camera is only turned on when the doorbell button is pressed
    #   - "motion": the camera is turned on automatically when motion is detected
    # If the user doesn’t specify anything, it defaults to "manual"
    parser.add_argument('--mode', type=str, default='manual', help='manual | motion')
    
    # Add an argument called '--secure'
    # This lets the user choose whether to run the web server with HTTPS (secure) or HTTP (not secure)
    # If the user doesn’t specify anything, it defaults to "off" (HTTP)
    parser.add_argument('--secure', type=str, default='off')
    
    # Read the arguments provided when the program is launched
    # For example: `python ring_server.py --mode motion --secure on`
    args = parser.parse_args()

    # === Initialize camera, GPIO, and audio ===

    # Start the Pygame mixer so we can play audio (like the doorbell sound)
    pygame.mixer.init()
    
    # Create a camera object to capture video using the Raspberry Pi camera
    camera = Picamera2()
    
    # Create an output object that will hold video frames to be streamed to the web app
    output = StreamingOutput()
    
    # Set up the doorbell button connected to GPIO pin 2
    # When this button is pressed, we’ll play a sound and possibly turn on the camera
    button = Button(2)
    
    # Set up a motion sensor connected to GPIO pin 4
    # This sensor can automatically trigger the camera when it detects movement
    pir = MotionSensor(4)

    # === Setup MQTT (Message Communication System) ===

    # Create an MQTT client that will send and receive messages over the network
    # 'transport="tcp"' means we’re using a basic, unencrypted connection
    client = paho.Client(transport="tcp")
    
    # Define what should happen when a new message is received from another device
    client.on_message = on_message
    
    # Define what should happen when the client successfully connects to the MQTT broker
    client.on_connect = on_connect
    
    # Define what should happen when the client gets disconnected from the broker
    client.on_disconnect = on_disconnect
    
    # Connect to the MQTT broker (server) running on the same device (localhost = 127.0.0.1)
    # Port 1883 is the standard port for unencrypted MQTT
    client.connect("127.0.0.1", 1883, 60)
    
    # Start a background loop that handles message sending/receiving
    client.loop_start()

    # === Setup audio playback ===

    # Create an audio streamer object that handles recording and sending audio
    # This uses a custom helper class defined in audioUtils.py
    audio_streamer = audioUtils.AudioPlayback()
    
    # Link the audio streamer to the MQTT client and tell it which topic to send audio on
    # In this case, the topic is "ring/audioresponse"
    # This allows audio captured from the microphone to be sent to the remote device
    audio_streamer.SetMQTTClient(client, "ring/audioresponse")
    
    # Set how many chunks of audio data to collect before sending it out
    # 80 chunks gives a balance between latency and audio quality
    audio_streamer.SetPlayBackFrameCount(80)

    # === Configure GPIO events ===

    # If the system is running in "motion" mode (as chosen from the command line),
    # set the motion sensor to call the function 'handleMotionMode' when movement is detected
    if args.mode == "motion":
        pir.when_motion = handleMotionMode
    
    # No matter the mode, set the doorbell button to call 'handleButtonMode' when pressed
    # This will play the bell sound and may turn on the camera, depending on the mode
    button.when_pressed = handleButtonMode
    
        # Start capturing frames in background
        threading.Thread(target=camera_capture_loop, daemon=True).start()

    # === Create HTTP or HTTPS server ===

    # Choose the port number based on whether secure mode is enabled
    # Use port 8001 for HTTPS (secure), or 8000 for HTTP (not secure)
    port = 8001 if args.secure == "on" else 8000
    
    # Create a server address that listens on all network interfaces ('' means any IP)
    server_address = ('', port)
    
    # Create the web server, using our custom handler (StreamingHandler) to respond to web requests
    httpd = StreamingServer(server_address, StreamingHandler)
    
    # If the user requested secure mode (HTTPS):
    if args.secure == "on":
        # Define the file paths to the TLS/SSL certificate and private key
        cert_path = "./certs/ring_server.crt"
        key_path = "./certs/ring_server.key"
    
        # Check if the certificate and key files exist
        if not os.path.exists(cert_path) or not os.path.exists(key_path):
            print("❌ TLS certs missing.")
            sys.exit(1)  # Exit the program if security files are missing
    
        # Set up the SSL context to enable encrypted communication (HTTPS)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    
        # Wrap the server’s socket with SSL so it uses HTTPS instead of HTTP
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    
        # Let the user know that the secure server is running
        print(f"🌐 HTTPS server on port {port}")

    # If not in secure mode, just start the basic HTTP server
    else:
        print(f"🌐 HTTP server on port {port}")
    
        # Start the server and keep it running forever
        try:
            httpd.serve_forever()  # This will keep the web server running until the user stops it
    
        # If the user presses Ctrl+C in the terminal, this will stop the server safely
        except KeyboardInterrupt:
            print("🛑 Shutting down...")
    
            # Disconnect from the MQTT broker
            client.disconnect()
    
            # Stop the MQTT loop that handles incoming messages
            client.loop_stop()
    
            # Stop the camera if it’s currently running
            camera.stop()
