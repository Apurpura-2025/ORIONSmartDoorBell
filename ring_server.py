# === Importing Useful Tools ===

# === These Python Libraries are needed to run the server ===
# Installed via setup_orion_doorbell.sh
import io, base64, sys, requests, threading, logging, socketserver    # Basic tools for input/output, networking, and logging
from http import server                         # Allows this program to act like a small server
import time, os, ssl, argparse, subprocess      # Tools for working with time, files, security, command-line arguments, and running other programs 
from gpiozero import Button, MotionSensor       # For using buttons and motion sensors connected to the Raspberry Pi
from picamera2 import Picamera2                 # Used to control Raspberry Pi camera
import paho.mqtt.client as paho                 # For sending messages over the internet or local network (used for communication between devices)
from threading import Condition                 # Used to safely share data between parts of the program that run at the same time
import pygame, cv2, numpy as np                 # For playing sounds (pygame), working with images (cv2), and doing math with arrays (numpy)
from dotenv import load_dotenv                  # Helps load settings from a hidden file (.env) like secret keys
import re                                       # For reading and matching patterns in text
import audioUtils                               # File made for playing and recording sound

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

# === Set Up Some Starting Conditions(global variables) ===
camera_on = False                          # Is the camera currently running? (No by default)
manual_override = False                    # Used in motion detection mode to stop the camera from turning back on automatically
manual_override_reset_time = 60            # If override is on, how long should we wait (in seconds) before turning it off?
manual_override_reset_thread = None        # This will hold a background timer to reset the override
output = None                              # Will later hold the video output that gets sent to the web app
selected_output_device = None              # Saves the name of the speaker being used
last_bell_time = 0                         # When was the last time the doorbell was pressed?
BELL_COOLDOWN_SECONDS = 5                  # How many seconds must pass before the bell can ring again

# === Class for MJPEG Streaming ===
class StreamingOutput:
    def __init__(self):
        self.frame = None                 # This will hold the most recent camera image
        self.condition = Condition()      # Threading condition to wait/notify frame updates. 
                                          # Used to safely let other parts of the program know when a new frame is ready

    def write(self, frame):
        _, jpeg = cv2.imencode('.jpg', frame)     # Encode OpenCV frame to JPEG. Convert image to JPEG format (web-friendly)
        with self.condition:                      # Lock access so this section is thread-safe
            self.frame = jpeg.tobytes()           # Save the JPEG image as bytes
            self.condition.notify_all()           # Wake up any waiting threads so they can send it to the browser

# === HTTP Request Handler for Web Interface ===
class StreamingHandler(server.BaseHTTPRequestHandler):
    # This helper function reads the content of a file (like an HTML, CSS, or JS file)
    # It will be used to send those files to the user's 
    def ReadClientApp(self, appfile, binary=False):
        # Open the file in binary ('rb') if it's an image or other binary file,
        # otherwise open it in normal text mode ('r') for things like HTML and JS
        with open(appfile, 'rb' if binary else 'r') as f:
            return f.read() # Read and return the contents of the file

    def do_GET(self):
        try:
            # === If the user types just the root address (like http://192.168.1.5/), redirect them to index.html ===
            if self.path == '/':
                self.send_response(301)                        # 301 means "redirect"
                self.send_header('Location', '/index.html')    # Tell the browser to go to /index.html
                self.end_headers()

            # === If the user is requesting the main webpage ===
            elif self.path == '/index.html':
                # Read the HTML file for the doorbell interface
                content = self.ReadClientApp("./wwwroot/html_pages/client_ring_app.html").encode("utf-8")
                self._send_file_response(content, 'text/html') # Send it to the browser as a webpage

            # === If the browser asks for the JavaScript file ===
            elif self.path == '/client_app.js':
                content = self.ReadClientApp('./wwwroot/js/client_app.js').encode("utf-8")
                self._send_file_response(content, 'application/javascript') # Send JS code to browser

            # === If the browser asks for the CSS (styling) file ===
            elif self.path == '/client_app_styles.css':
                content = self.ReadClientApp('./wwwroot/css/client_app_styles.css').encode("utf-8")
                self._send_file_response(content, 'text/css') # Send CSS to style the page

            # === If the browser is trying to start the camera video stream ===
            elif self.path.startswith('/stream.mjpg'):
                self._handle_stream()     # Start sending camera images one after another

            # === If the path doesn’t match any of the above, show a 404 error ===
            else:
                self.send_error(404) # Page not found
                
        except Exception as e:
            # If something goes wrong, log the error for debugging
            logging.error(f"Handler error: {e}")

    def _send_file_response(self, content, content_type):
        self.send_response(200)                            # Send a "200 OK" response to let the browser know the request was successful
        self.send_header('Content-type', content_type)     # Tell the browser what type of file is being sent (HTML, JavaScript, CSS, etc.)
        self.send_header('Content-Length', len(content))   # Tell the browser how big the file is (in bytes)
        self.send_header('Cache-Control', 'no-cache')      # Tell the browser not to store a cached copy — always ask for a fresh one
        self.end_headers()                                 # Finish sending the headers
        self.wfile.write(content)                          # Now actually send the content of the file to the browser

    def _handle_stream(self):
        print("📡 MJPEG stream requested")     # Show in the terminal that a video stream was requested, for debugging
        self.send_response(200)                 # Tell the browser we’re sending a live stream
        self.send_header('Cache-Control', 'no-cache, private')   # Tell the browser not to cache (save) this stream
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')  # Tell the browser we’re sending multiple JPEG images in a row ("multipart stream")
        self.end_headers()      # Finish sending headers
        try:
            while True:
                # Wait until a new frame (image) is available
                with output.condition:
                    output.condition.wait(timeout=1) # Wait up to 1 second
                    frame = output.frame             # Get the latest camera frame
                if frame:
                    # Start a new image section
                    self.wfile.write(b'--FRAME\r\n')

                    # Send image headers
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()

                    # Send the actual image data
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')    # End the image section
                    self.wfile.flush()           # Make sure it gets sent immediately
        except (BrokenPipeError, ConnectionResetError):
             # If the user closes the browser or the connection breaks, just log a warning
            logging.warning("⚠️ MJPEG stream broken")

# === Threaded HTTP Server ===
class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    # This class creates a special web server that can handle multiple users at once
    allow_reuse_address = True # Allow the server to quickly restart without waiting for the port to be freed
    daemon_threads = True      # Run each connection in the background (so the server doesn’t get stuck)

# === Camera Frame Capture Loop ===
def camera_capture_loop():
    global camera_on # This tells the function whether the camera should be running
    while True:      # Keep running this loop forever
        if not camera_on:
            time.sleep(0.1)  # If the camera is off, wait a short time and check again
            continue         # Skip the rest and restart the loop
        try:
            # Take a picture (called a frame) from the camera
            frame = camera.capture_array()    # This gives the image as a NumPy array (used by OpenCV)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) # Convert the color format from BGR (used by OpenCV) to RGB (used by most other systems)
            output.write(rgb_frame)           # Save the image so it can be shown on the website
            time.sleep(1 / 24)                # Wait a tiny bit to target about 24 frames per second (like a movie)
        except Exception as e:
            print("⚠️ Frame capture error:", e) # If something goes wrong (e.g., camera error), show a warning

# === Turn Camera On or Off ===
def cameraControl(mode):
    global camera_on        # This keeps track of whether the camera is currently running

    # === Turn the camera ON ===
    if mode == "on" and not camera_on:
        camera.configure(camera.create_video_configuration(main={"size": (640, 480)})) # Set up the camera with a resolution of 640x480
        camera.start()      # Start the camera

        # Manually adjust camera settings:
        # - AwbMode 0: Turns off automatic white balance
        # - ColourGains: Boosts red and blue to fix color tones
        camera.set_controls({
            "AwbMode": 0,
            "ColourGains": (1.5, 2)  
        })
        camera_on = True             # Update the status to say the camera is on
        print("📸 Camera started")   # Start capturing frames in the background
        threading.Thread(target=camera_capture_loop, daemon=True).start()

     # === Turn the camera OFF ===
    elif mode == "off" and camera_on:
        camera.stop()                # Stop the camera
        camera_on = False            # Update the status
        print("🛑 Camera stopped")

# === Start Camera from App or Motion ===
def startCamera():
    global manual_override          # This flag prevents the camera from turning on again too soon in motion mode
    if not camera_on:               # Only turn on the camera if it's currently off
        cameraControl("on")         # Call the function to turn on the camera
        client.publish(REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC, "on")    # Send a message over MQTT so the app knows the camera is now on
        if args.mode == "motion":    # If we're using motion detection mode...
            manual_override = False  # Allow motion to trigger the camera again in the future

# === Stop Camera and Activate Override ===
def stopCamera():
    global manual_override, manual_override_reset_thread  # Use shared variables to manage override behavior
    if camera_on:                                         # Only stop the camera if it’s currently running
        cameraControl("off")                              # Turn the camera off
        client.publish(REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC, "off")    # Let the web app know the camera has been turned off
        print("🚫 Manual stop triggered — override active.")
        # If we're in motion-detection mode...
        if args.mode == "motion":
            manual_override = True            # Temporarily block motion from turning the camera back on
            # Start a timer to automatically reset the override later (if one isn't already running)
            if not manual_override_reset_thread or not manual_override_reset_thread.is_alive():
                manual_override_reset_thread = threading.Thread(target=reset_manual_override, daemon=True)
                manual_override_reset_thread.start()

# === Reset Manual Override After Delay ===
def reset_manual_override():
    global manual_override            # Use the shared override setting
    print(f"🕒 Manual override reset in {manual_override_reset_time}s") # Let the user know how long the override will last
    time.sleep(manual_override_reset_time)    # Wait for the specified number of seconds (usually 60)
    manual_override = False                   # Turn off the override so motion detection can trigger the camera again
    print("✅ Manual override lifted.")       # Let the user know it's OK to use motion detection again

# === Motion Sensor Trigger ===
def handleMotionMode():
    global manual_override                # This flag temporarily blocks the camera from turning on again too soon
    print("👀 Motion detected!")         # Let the user know motion was sensed
    
    # Only turn on the camera if:
    # - It's currently off
    # - It's not being blocked by the override
    if not camera_on and not manual_override:
        startCamera()        # Turn on the camera
    else:
        print("🛑 Motion ignored.")    # If camera is already on or override is active, do nothing

# === Button Press Trigger ===
def handleButtonMode():
    startCamera()    # When the doorbell button is pressed, turn on the camera

# === List ALSA Audio Output Devices ===
def list_alsa_playback_devices():
    try:
        result = subprocess.run(["aplay", "-L"], capture_output=True, text=True, check=True)  # Run the command "aplay -L" to list all sound output devices
        return [line.strip() for line in result.stdout.splitlines() if line and not line.startswith(" ")] # Go through each line of the result, remove spaces, and return a clean list of device names
    except subprocess.CalledProcessError as e:
        print("❌ Error listing ALSA devices:", e) # If something goes wrong, print an error message
        return []                                   # Return an empty list if there was a problem

# === Select a Bluetooth Audio Device if Available ===
def select_bluetooth_output_device(preferred_keywords=["bluealsa", "bluetooth", "BT"]):
    global selected_output_device        # Keep track of the chosen speaker (so we don't repeat the search)
    if selected_output_device:           # If we already selected a speaker before, just reuse it
        return selected_output_device
    for device in list_alsa_playback_devices():    # Go through the list of available sound output devices
        for keyword in preferred_keywords:            # Look for devices that have Bluetooth-related names
            if keyword.lower() in device.lower():
                selected_output_device = device       # Save the selected device name
                print(f"✅ Selected BT device: {device}")    # Show which one was chosen
                return device
    # If no Bluetooth speaker was found, use the system default
    selected_output_device = "default"
    print("⚠️ No BT device found. Using 'default'")
    return selected_output_device

def get_bt_sink_name():
    try:
        # Run a command to list all available audio sinks (output devices) using PulseAudio
        result = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True)
        # Go through each line of the result
        for line in result.stdout.splitlines():
            if "bluez_output" in line:    # Look for a line that includes "bluez_output" (which means it's a Bluetooth speaker)
                return line.split()[1]    # Return the name of the Bluetooth sink (the speaker's ID)
    except Exception as e:    # If something goes wrong (like the command fails), show an error message
        print("❌ Could not find Bluetooth sink:", e)
    return None    # If no Bluetooth speaker was found, return None

def get_current_volume_percent(sink):
    try: # Run a system command to get detailed info about all sound output devices
        result = subprocess.run(["pactl", "list", "sinks"], capture_output=True, text=True)
        inside_sink = False    # Flag to know when we're looking at the right speaker (sink)
        for line in result.stdout.splitlines():    # Go through each line of the output
            if sink in line:     # If we find the sink we're looking for, mark that we're inside its info block
                inside_sink = True
            
            # If we're inside the sink block and we find a line that shows volume
            elif inside_sink and "Volume:" in line and "Channel" not in line:
                match = re.search(r"(\d+)%", line) # Look for a number followed by a % (like "45%")
                if match:
                    return int(match.group(1))     # Return the volume as an integer

            # If we hit a blank line while inside the sink block, we’ve reached the end
            elif inside_sink and line.strip() == "":
                break  # end of this sink's block
    except Exception as e:
        print("❌ Could not get volume:", e)    # If something goes wrong, show an error message
    return None        # If volume wasn't found, return None

def change_volume(direction):
    # Step 1: Find the name of the Bluetooth speaker (sink)
    sink = get_bt_sink_name()
    if not sink:
        print("⚠️ Bluetooth sink not found.")
        return    # Stop if no Bluetooth speaker is connected
    # Step 2: Get the current volume level of the speaker    
    current = get_current_volume_percent(sink)
    if current is None:
        print("⚠️ Could not read current volume.")
        return    # Stop if we can’t find out the current volume
        
    # Step 3: Decide the new volume based on the direction ("up" or "down")
    new_volume = current + 5 if direction == "up" else current - 5
    
    # Step 4: Make sure the volume stays between 0% and 100%
    new_volume = max(0, min(100, new_volume)) 

    try:    # Step 5: Use a system command to set the new volume
        subprocess.run(["pactl", "set-sink-volume", sink, f"{new_volume}%"], check=True)
        print(f"🔊 Volume set to {new_volume}%")    # Confirm the change
    except subprocess.CalledProcessError as e:
        print(f"❌ Volume change error: {e}")       # If the command fails, print an error
        
def handleButtonMode():
    global last_bell_time    # Keeps track of the last time the button was pressed
    now = time.time()        # Get the current time in seconds
    if now - last_bell_time < BELL_COOLDOWN_SECONDS:    # If the bell was pressed too recently, ignore this press to prevent spamming
        print("⏳ Bell on cooldown. Ignoring press.")
        return  # Exit early and do nothing
    last_bell_time = now # Update the time so we know when the bell was last pressed
    
    # === Play bell sound using ffplay (a media player) ===
    try:
        env = os.environ.copy()    # Copy the current environment variables
        env["DISPLAY"] = ":0"      # Needed for desktop audio routing
        env["PULSE_RUNTIME_PATH"] = f"/run/user/{os.getuid()}/pulse"  # Path to the audio system
        # Start playing the bell sound as a background process
        subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "./sounds/bell1.mp3"],    # Play without video, exit when done
            stdout=subprocess.DEVNULL,    # Don’t show output in terminal
            stderr=subprocess.DEVNULL,
            env=env        # Use the updated environment
        )
        print("🔔 Bell sound played with ffplay")
    except Exception as e:
        print(f"❌ Failed to play bell sound: {e}")

    # === If the system is in manual mode, also turn on the camera ===
    if args.mode == "manual":
        startCamera()    # Start the camera feed

# === Send Image to OpenAI GPT-4o and Publish Response ===
def handleGPTRequest():
    
    #Comment out this line when AI integration is enabled
    client.publish(GPT_RESPONSE_TOPIC, payload="Awaiting AI integration...", qos=0, retain=False)
    
    ## AI integration. Delete """ on either end to enable.
    """
    # This line tells the app we're waiting for the AI's response
    client.publish(GPT_RESPONSE_TOPIC, payload="waiting for the AI to Answer...", qos=0, retain=False)
    try:
        # === Step 1: Capture an image from the camera and store it in memory ===
        buffer = io.BytesIO()    # Create an in-memory buffer to store image data
        camera.capture_file(buffer, format='jpeg')    # Take a picture and save it to the buffer
        buffer.seek(0)            # Rewind to the beginning of the buffer so we can read it
        # === Step 2: Convert the image to base64 format (needed for sending to GPT) ===
        img_b64 = base64.b64encode(buffer.read()).decode('utf-8')

        # === Step 3: Prepare the request to send to OpenAI's GPT-4o model ===
        payload = {
            "model": "gpt-4o",    # Tells OpenAI which AI model to use
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe the image in detail in 2-3 sentences."},
                    {"type": "image_url", "image_url": { "url": f"data:image/jpeg;base64,{img_b64}" }}
                ]
            }],
            "max_tokens": 400    # Limits the length of the AI’s response
        }

        # === Step 4: Set up headers, including your OpenAI API key ===
        headers = { "Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json" } # Authenticates the request

        # === Step 5: Send the image and prompt to OpenAI ===
        response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()        # Raises an error if the request fails

        # === Step 6: Extract and print the AI's response ===
        result = response.json()['choices'][0]['message']['content']
        print("🤖 GPT:", result)

         # === Step 7: Send the result back to the web app via MQTT ===
        client.publish(GPT_RESPONSE_TOPIC, payload=result, qos=0, retain=False)

    except Exception as e:    # If something goes wrong, show and send an error message
        error_msg = f"❌ GPT error: {e}"
        print(error_msg)
        client.publish(GPT_RESPONSE_TOPIC, payload=error_msg, qos=0, retain=False)
        """

# === MQTT Callback Handlers ===
def on_message(client, userdata, msg):
    topic = msg.topic           # Get the topic (channel) of the message
    print("📩 MQTT:", topic)    # Print the topic to the terminal for debugging
    
    # === 1. Handle Camera On/Off Request ===
    if topic == REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC:
        cameraControl(msg.payload.decode())    # Turn the camera on or off based on the message content ("on" or "off")
    
    # === 2. Handle Microphone Control Request ===
    elif topic == REMOTE_APP_MICROPHONE_CONTROL_TOPIC:
        command = msg.payload.decode().lower()    # Get the command: "on" or "off"
        print("🎤 Microphone control:", command)
        if command == "on":
            audio_streamer.StartPlaying()    # Start streaming microphone audio
        elif command == "off":
            audio_streamer.StopPlaying()     # Stop streaming audio
    
    # === 3. Handle GPT Image Description Request ===
    elif topic == GPT_REQUEST_TOPIC:
        threading.Thread(target=handleGPTRequest, daemon=True).start()    # Start a new background thread to handle the AI image description

    # === 4. Handle Incoming Audio from the Web App ===
    elif topic == REMOTE_APP_AUDIO_DATA_TOPIC:
        print("🔈 Audio chunk received — converting and playing.")
        try:
            import tempfile    # Used to temporarily save the audio file

             # Save the received audio data (webm format) to a temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as raw_file:
                raw_file.write(msg.payload)
                raw_file.flush()
                raw_path = raw_file.name

            # Convert the webm file to wav format using ffmpeg
            wav_path = raw_path.replace(".webm", ".wav")
            subprocess.run(["ffmpeg", "-y", "-i", raw_path, wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["aplay", wav_path]) # Play the converted audio using aplay (built-in audio player)

            # Delete the temporary files after use
            os.unlink(raw_path)
            os.unlink(wav_path)

        except Exception as e:
            print("❌ Audio playback failed:", e)
    
    # === 5. Handle Volume Change Request ===
    elif topic == VOLUME_CONTROL_TOPIC:
        direction = msg.payload.decode()
        print(f"🔊 Volume change requested: {direction}")
        change_volume(direction)

def on_connect(client, userdata, flags, rc, properties=None):
    print("✅ MQTT connected:", rc)        # Confirm that the system connected to the MQTT server
    
     # Subscribe to each topic so the Raspberry Pi can listen for specific messages
    for t in [REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC,     # For turning the camera on/off
              GPT_REQUEST_TOPIC,                         # For requesting an image description from GPT
              REMOTE_APP_MICROPHONE_CONTROL_TOPIC,       # For turning the microphone on/off
              REMOTE_APP_AUDIO_DATA_TOPIC,               # For receiving audio sent from the app
              VOLUME_CONTROL_TOPIC]:                     # For increasing/decreasing speaker volume
        client.subscribe(t)                # Tell MQTT: "I want to hear messages sent to this topic"
    print("📡 Subscribed to all topics.") # Let the user know it's ready to receive commands

def on_disconnect(client, userdata, flags, rc, properties=None):
    print("🔌 MQTT disconnected:", rc)    # Show a message when the system loses connection to MQTT
    stopCamera()                           # Turn off the camera as a safety step

# === Main Program Execution ===
if __name__ == '__main__':
    # === 1. Define MQTT Topics (Communication Channels) ===
    # These are like labeled mailboxes for different features
    REMOTE_APP_CAMERA_ONOFF_CONTROL_TOPIC = "ring/remote_app_control/camera"
    REMOTE_DEV_CAMERA_ONOFF_CONTROL_TOPIC = "ring/local_dev_control/camera"
    REMOTE_APP_MICROPHONE_CONTROL_TOPIC = "ring/remote_app_control/microphone"
    REMOTE_APP_AUDIO_DATA_TOPIC = "ring/remote_app_audio_data"
    GPT_REQUEST_TOPIC = "ring/gptrequest"
    GPT_RESPONSE_TOPIC = "ring/gptresponse"
    VOLUME_CONTROL_TOPIC = "ring/remote_app_control/volume"

    # === 2. Read Options From the Command Line ===
    # Example: --mode motion or --secure on
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', type=str, default='manual', help='manual | motion')
    parser.add_argument('--secure', type=str, default='off')
    args = parser.parse_args()

    # === 3. Set Up Hardware and Systems ===
    pygame.mixer.init()            # Start sound system
    camera = Picamera2()           # Create camera object
    output = StreamingOutput()     # Prepare video stream manager
    button = Button(2)             # Button connected to GPIO pin 2
    pir = MotionSensor(4)          # Motion sensor on GPIO pin 4

    # === 4. Connect to MQTT (Messaging System) ===
    client = paho.Client(transport="tcp")        # Use plain TCP for local MQTT
    client.on_message = on_message               # Define what to do when messages arrive
    client.on_connect = on_connect               # Define what to do when connected
    client.on_disconnect = on_disconnect         # Define what to do when disconnected
    client.connect("127.0.0.1", 1883, 60)        # Connect to local MQTT broker
    client.loop_start()                          # Start MQTT client in the background

    # === 5. Prepare Audio Streaming ===
    audio_streamer = audioUtils.AudioPlayback()
    audio_streamer.SetMQTTClient(client, "ring/audioresponse")    # Topic for voice data
    audio_streamer.SetPlayBackFrameCount(80)                 # Buffer size for streaming

    # === 6. Set What Each Sensor Does ===
    if args.mode == "motion":
        pir.when_motion = handleMotionMode    # Motion sensor triggers the camera
    button.when_pressed = handleButtonMode    # Button press triggers bell + camera

    # === 7. Start Capturing Video Frames in the Background ===
    threading.Thread(target=camera_capture_loop, daemon=True).start()

    # === 8. Start the Web Server (HTTPS if secure mode is on) ===
    port = 8001 if args.secure == "on" else 8000
    server_address = ('', port)
    httpd = StreamingServer(server_address, StreamingHandler)

    if args.secure == "on":
        cert_path = "./certs/ring_server.crt"
        key_path = "./certs/ring_server.key"
        if not os.path.exists(cert_path) or not os.path.exists(key_path):
            print("❌ TLS certs missing.")    # Can't run secure server without certs
            sys.exit(1)
        # Create a secure HTTPS context
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        print(f"🌐 HTTPS server on port {port}")
    else:
        print(f"🌐 HTTP server on port {port}")

     # === 9. Keep the Server Running Until Manually Stopped ===
    try:
        httpd.serve_forever()    # Start the web server
    except KeyboardInterrupt:    # If someone presses Ctrl+C...
        print("🛑 Shutting down...")
        client.disconnect()      # Disconnect from MQTT
        client.loop_stop()       # Stop MQTT background process
        camera.stop()            # Turn off the camera
