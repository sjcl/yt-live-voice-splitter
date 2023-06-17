import argparse
import os
import time
import yt_dlp
import shutil
import fnmatch
import subprocess
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from faster_whisper import WhisperModel

from splitter import Splitter


SAMPLING_RATE = 16000

def recreate_directory(directory_path):
    if os.path.exists(directory_path):
        shutil.rmtree(directory_path)
    os.makedirs(directory_path, exist_ok=True)

def get_audio_url(url):
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": "tmp/temp_audio.aac",
        "noplaylist": True,
        "quiet": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = info.get("formats", None)
        audio_url = formats[0]["url"] 
        logging.debug(info)

    return audio_url

def run_ffmpeg(audio_url):
    split_command = f"ffmpeg -i {audio_url} -f segment -segment_time {chunk_size} -ac 1 -ar {SAMPLING_RATE} -vn tmp/audio_%03d.wav"
    devnull = open('/dev/null', 'w')
    return subprocess.Popen(split_command, shell=True, stdout=devnull, stderr=devnull)

class FileHandler(FileSystemEventHandler):

    def __init__(self):
        self.previous_file = None

    def on_created(self, event):
        if not os.path.isdir(event.src_path):
            file_name = os.path.basename(event.src_path)
            if fnmatch.fnmatch(file_name, '*.wav'):
                if self.previous_file:
                    splitter.process_audio(self.previous_file)
                self.previous_file = event.src_path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real-time Voice Activity Detection")
    parser.add_argument("url", help="Livestream URL")
    parser.add_argument("--chunk_size", type=int, default=3, help="Chunk size")
    parser.add_argument("--threshold", type=int, default=SAMPLING_RATE, help="Threshold for a sentence to split (frame)")
    parser.add_argument("--margin", type=int, default=SAMPLING_RATE / 2, help="Margin to be added before and after splitting (frame)")
    parser.add_argument("--debug", action="store_true")

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level = logging.DEBUG)

    url = args.url
    chunk_size = int(args.chunk_size)
    threshold = int(args.threshold)
    margin = int(args.margin)

    recreate_directory("tmp")
    recreate_directory("result")

    splitter = Splitter(sampling_rate=SAMPLING_RATE, threshold=threshold, margin=margin)

    # whisper_model = WhisperModel("large-v2", device="cuda", compute_type="float16")

    audio_url = get_audio_url(url)

    event_handler = FileHandler()
    observer = Observer()
    observer.schedule(event_handler, "tmp", recursive=False)
    observer.start()

    ffmpeg = run_ffmpeg(audio_url)
    try:
        while True:
            time.sleep(1)
            if ffmpeg.returncode is not None:
                splitter.on_exit()
                break
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
