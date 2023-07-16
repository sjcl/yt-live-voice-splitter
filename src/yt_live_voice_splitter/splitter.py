from __future__ import annotations
import torch
import os
import wave
import fnmatch
from watchdog.events import FileSystemEventHandler

class Splitter:

    def __init__(self, sampling_rate: int, threshold: int, margin: int) -> None:
        self.sampling_rate = sampling_rate
        self.threshold = threshold
        self.margin = margin
    
        self.vad_model, vad_utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                              model='silero_vad',
                              force_reload=True,
                              onnx=True)
        
    def load_chunk(self, file_path) -> Chunk:
        with wave.open(file_path, 'rb') as wav_file:
            num_channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            num_frames = wav_file.getnframes()
            audio_data = wav_file.readframes(num_frames)
        return self.Chunk(audio_data, num_channels, sample_width, num_frames)
        
    def process_audio(self, file_path) -> None:
        chunk = self.Chunk(file_path)
        self.prev_chunk = chunk

    class Chunk:

        def __init__(self, file_path: str, vad_model, vad_utils) -> None:
            self.file_path = file_path
            self.vad_model = vad_model
            (self.get_speech_timestamps, _, self.read_audio, _, _) = vad_utils
            
            self.audio_data, self.num_channels, self.sample_width, self.num_frames = self.read_wav_file(self.file_path)

        @staticmethod
        def read_wav_file(file_path):
            with wave.open(file_path, 'rb') as wav_file:
                num_channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                num_frames = wav_file.getnframes()
                audio_data = wav_file.readframes(num_frames)
            return audio_data, num_channels, sample_width, num_frames
        
    class Sentence:

        def __init__(self) -> None:
            pass
        
class SplitterFileHandler(FileSystemEventHandler):

    def __init__(self, splitter: Splitter):
        self.splitter = splitter
        self.previous_file = None

    def on_created(self, event):
        if not os.path.isdir(event.src_path):
            file_name = os.path.basename(event.src_path)
            if fnmatch.fnmatch(file_name, '*.wav'):
                if self.previous_file:
                    self.splitter.process_audio(self.previous_file)
                self.previous_file = event.src_path
