import pyaudio
import threading
import logging
import io
import wave

class AudioInputStream:
    def __init__(self, sample_rate=44100, channels=1, chunk_size=1024):
      # Suppress PyAudio logging
      logging.getLogger('pyaudio').setLevel(logging.WARNING)

      # Initialize PyAudio
      self.audio = pyaudio.PyAudio()
      self._sample_rate = sample_rate
      self._channels = channels
      self._chunk_size = chunk_size

    def Open(self):
       # Open input stream
        self.input_stream = self.audio.open(format=pyaudio.paInt16,
            channels=self._channels,
            rate=self._sample_rate,
            input=True,
            frames_per_buffer=self._chunk_size)
        self.sample_size = self.audio.get_sample_size(pyaudio.paInt16)

    def SampleSize(self):
        return self.sample_size
 
    def ReadData(self):
       return self.input_stream.read(self._chunk_size)
    
    def Terminate(self):
          self.audio.terminate()

    def Close(self):
        # Stop and close the streams
        self.input_stream.stop_stream()
        self.input_stream.close()
        # self.audio.terminate()

    def __enter__(self):
      self.Open()
      return self.input_stream

    def __exit__(self, *args):
       self.Close()



class AudioOutputStream:
    def __init__(self, sample_rate=44100, channels=1, frames_per_buffer=1024):    
      # Initialize PyAudio
      self.audio = pyaudio.PyAudio()
      self._sample_rate = sample_rate
      self._channels = channels
     # self._frames_per_buf = frames_per_buffer
     
    def Open(self):
        self.output_stream = self.audio.open(format=pyaudio.paInt16,
                            channels=self._channels,
                            rate=self._sample_rate,
                            output=True)
                            #frames_per_buffer=self._frames_per_buf)
    
    def Close(self):
       # Stop and close the streams
        self.output_stream.stop_stream()
        self.output_stream.close()
      
    def Terminate(self):
          self.audio.terminate()
           
    def WriteData(self, data):
       self.output_stream.write(data)

    def __enter__(self):
      # Open input stream
      self.Open()
      return self.output_stream
    
    def __exit__(self, *args):
        self.Close()


class AudioPlayback:
   def __init__(self, sample_rate=44100, channels=1, chunk_size=1024):
      self.input = AudioInputStream(sample_rate, channels, chunk_size)
      # self.output = AudioOutputStream(sample_rate, channels)
      self.lock = threading.Lock()
      self.is_playing = False
      self.playback_frame_count = 80
      # self.playback_thread = threading.Thread(target=self._playback)
      
   def SetPlayBackFrameCount(self, frame_count):
       self.playback_frame_count = frame_count

   def SetMQTTClient(self, client, topic):
       self.client = client
       self.topic = topic
   
   def IsPlaying(self):
        with self.lock:
            return self.is_playing
      
   def SetIsPlaying(self,state):
        with self.lock:
            self.is_playing = state
      
   def StartPlaying(self):
        if self.IsPlaying():
            print("⏳ Already streaming audio. Ignoring new request.")
            return
        if not self.IsPlaying():
            self.input.Open()
            # uncomment to echo to microphone
            # self.output.Open()
            self.SetIsPlaying(True)   
            self.playback_thread = threading.Thread(target=self._playback)
            self.playback_thread.start()

   def StopPlaying(self):
        if self.IsPlaying():
            self.SetIsPlaying(False)
            self.playback_thread.join()
            self.input.Close()
            # self.output.Close()
      
   def _playback(self):
        frames = []
        print("Audio playback started")
        while self.IsPlaying():
            data = self.input.ReadData()
            frames.append(data)
            if len(frames) >= self.playback_frame_count:
                buffer = io.BytesIO()
                with wave.open(buffer, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(self.input.SampleSize())
                    wf.setframerate(44100)
                    wf.writeframes(b''.join(frames))
                
                wav_data = buffer.getvalue()
                print("📤 Publishing audio:", len(wav_data))
                self.client.publish(self.topic, payload=wav_data, qos=0, retain=False)
                frames = []

   def Close(self):
         self.StopPlaying()
        # if hasattr(obj, "_playback"):
         self.input.Terminate()
         # self.output.Terminate()
        
         print("Audio playback closed ")


          



       