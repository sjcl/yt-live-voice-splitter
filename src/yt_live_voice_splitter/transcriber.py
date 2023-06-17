import os
import fnmatch
from faster_whisper import WhisperModel
from watchdog.events import FileSystemEventHandler

class Transcriber:

    def __init__(self):
        self.model = WhisperModel("large-v2", device="cuda", compute_type="float16")

    def process_audio(self, audio_path):
        segments, info = self.model.transcribe(audio_path, language="ja")
        for segment in segments:
            print(segment.text)


class TranscriberFileHandler(FileSystemEventHandler):

    def __init__(self, transcriber: Transcriber):
        self.transcriber = transcriber

    def on_created(self, event):
        if not os.path.isdir(event.src_path):
            file_name = os.path.basename(event.src_path)
            if fnmatch.fnmatch(file_name, '*.wav'):
                self.transcriber.process_audio(event.src_path)
