import argparse
import asyncio
import os
import signal
import time
import traceback
import yt_dlp
import collections
import sys
import shutil
import wave
import fnmatch
import torch
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from faster_whisper import WhisperModel


SAMPLING_RATE = 16000

chunk_size = None
threshold = None
margin = None

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
        print(info)

    return audio_url

def run_ffmpeg(audio_url):
    split_command = f"ffmpeg -i {audio_url} -f segment -segment_time {chunk_size} -ac 1 -ar {SAMPLING_RATE} -vn tmp/audio_%03d.wav"
    devnull = open('/dev/null', 'w')
    return subprocess.Popen(split_command, shell=True, stdout=devnull, stderr=devnull)

def read_wav_file(file_path):
    with wave.open(file_path, 'rb') as wav_file:
        num_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        num_frames = wav_file.getnframes()
        audio_data = wav_file.readframes(num_frames)
    return audio_data, num_channels, sample_width, num_frames

def write_wav_file(file_path, audio_data, sample_width):
    with wave.open(file_path, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(SAMPLING_RATE)
        wav_file.writeframes(audio_data)

connecting_audio = None
last_audio = None
file_count = 1

def process_audio(audio_path):
    global connecting_audio, last_audio, file_count
    # whisper_model = WhisperModel("large-v2", device="cuda", compute_type="float16")

    # audio_url = get_audio_url(url)

    # split_command = f"ffmpeg -i {audio_url} -f segment -segment_time {chunk_size} -ac 1 -ar {SAMPLING_RATE} -vn tmp/audio_%03d.wav"
    # devnull = open('/dev/null', 'w')
    # # process = await asyncio.create_subprocess_shell(split_command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    # process = await asyncio.create_subprocess_shell(split_command, stdout=devnull, stderr=devnull)

    # await asyncio.sleep(chunk_size)

    file_num = int(os.path.basename(audio_path).split("_")[1].split(".")[0])

    audio = read_audio(audio_path, sampling_rate=SAMPLING_RATE)
    speech_segments = get_speech_timestamps(audio, vad_model, sampling_rate=SAMPLING_RATE)
    
    audio_data, num_channels, sample_width, frame_length = read_wav_file(audio_path)

    print(f"Processing: {audio_path} length: {frame_length} Segments: {speech_segments}")

    if connecting_audio and not connecting_audio['out']:
        if speech_segments:
            start = speech_segments[0]['start']

            for i in range(len(speech_segments)):
                current_start = speech_segments[i]['start']
                current_end = speech_segments[i]['end']

                if i == 0 and connecting_audio['length_to_end'] + current_start >= threshold:
                    output_file_path = os.path.join("result", f"audio_{file_count}.wav")

                    if connecting_audio['length_to_end'] >= margin:
                        out_audio = connecting_audio['audio_data'][ : (connecting_audio['end_frame'] + margin) * sample_width]
                        write_wav_file(output_file_path, out_audio, sample_width)
                        print(f"{output_file_path} code: 1")
                    else:
                        out_audio = connecting_audio['audio_data'] + audio_data[ : (margin - connecting_audio['length_to_end']) * sample_width]
                        write_wav_file(output_file_path, out_audio, sample_width)
                        print(f"{output_file_path} code: 2")
                        connecting_audio['end_frame'] = connecting_audio['length_to_end']
                        connecting_audio['length_to_end'] = frame_length - connecting_audio['length_to_end']
                        connecting_audio['last_file_num'] = file_num
                    connecting_audio['audio_data'] = None
                    connecting_audio['out'] = True
                    file_count += 1
                else:
                    if connecting_audio['out'] is False:
                        if current_end + margin > frame_length or frame_length - current_end < threshold:
                            if connecting_audio['last_file_num'] < file_num:
                                connecting_audio['audio_data'] = connecting_audio['audio_data'] + audio_data
                                connecting_audio['last_file_num'] = file_num
                            elif connecting_audio['end_frame'] + 1 < frame_length:
                                connecting_audio['audio_data'] = connecting_audio['audio_data'] + audio_data[(connecting_audio['end_frame'] + 1) * sample_width : ]
                            connecting_audio['end_frame'] = speech_segments[-1]['end']
                            connecting_audio['length_to_end'] = frame_length - speech_segments[-1]['end']
                            break

                        next_start = speech_segments[i + 1]['start'] if i < len(speech_segments) - 1 else None

                        if next_start is None or next_start - current_end >= threshold:
                            out_audio = connecting_audio['audio_data'] + audio_data[ : (current_end + margin) * sample_width] if connecting_audio['last_file_num'] < file_num else connecting_audio['audio_data'] + audio_data[(connecting_audio['end_frame'] + 1) * sample_width : (current_end + margin) * sample_width]
                            output_file_path = os.path.join("result", f"audio_{file_count}.wav")
                            write_wav_file(output_file_path, out_audio, sample_width)
                            print(f"{output_file_path} code: 3")
                            connecting_audio['audio_data'] = None
                            connecting_audio['end_frame'] = current_end
                            connecting_audio['length_to_end'] = frame_length - current_end
                            connecting_audio['last_file_num'] = file_num
                            connecting_audio['out'] = True
                            file_count += 1
                        else:
                            if connecting_audio['last_file_num'] < file_num:
                                connecting_audio['audio_data'] = connecting_audio['audio_data'] + audio_data[ : current_end * sample_width]
                                connecting_audio['last_file_num'] = file_num
                            else:
                                connecting_audio['audio_data'] = connecting_audio['audio_data'] + audio_data[(connecting_audio['end_frame'] + 1) * sample_width : current_end * sample_width]
                            connecting_audio['end_frame'] = current_end
                            connecting_audio['length_to_end'] = frame_length - current_end
                    else:
                        if current_end + margin > frame_length or frame_length - current_end < threshold:
                            if connecting_audio['last_file_num'] < file_num:
                                connecting_audio['audio_data'] = audio_data
                            else:
                                if connecting_audio['end_frame'] + 1 < frame_length:
                                    connecting_audio['audio_data'] = audio_data[(connecting_audio['end_frame'] + 1) * sample_width : ]
                                else:
                                    continue
                            connecting_audio['end_frame'] = current_end
                            connecting_audio['length_to_end'] = frame_length - current_end
                            connecting_audio['last_file_num'] = file_num
                            connecting_audio['out'] = False
                            break

                        margin_start = start - margin
                        next_start = speech_segments[i + 1]['start'] if i < len(speech_segments) - 1 else None
                        
                        if next_start is None or next_start - current_end >= threshold:
                            output_file_path = os.path.join("result", f"audio_{file_count}.wav")
                            out_oudio = last_audio[margin_start * sample_width : ] + audio_data[ : (current_end + margin) * sample_width] if last_audio is not None and margin_start < 0 else audio_data[max(0, margin_start) * sample_width : (current_end + margin) * sample_width]
                            write_wav_file(output_file_path, out_oudio, sample_width)
                            print(f"{output_file_path} code: 4")
                            file_count += 1
                            start = next_start
        else:
            output_file_path = os.path.join("result", f"audio_{file_count}.wav")
            if connecting_audio['length_to_end'] >= margin:
                out_audio = connecting_audio['audio_data'][ : (connecting_audio['end_frame'] + margin) * sample_width]
                write_wav_file(output_file_path, out_audio, sample_width)
                print(f"{output_file_path} code: 5")
            else:
                out_audio =connecting_audio['audio_data'] + audio_data[ : (margin - connecting_audio['length_to_end']) * sample_width]
                write_wav_file(output_file_path, out_audio, sample_width)
                print(f"{output_file_path} code: 6")
                connecting_audio['end_frame'] = connecting_audio['length_to_end']
                connecting_audio['length_to_end'] = frame_length - connecting_audio['length_to_end']
                connecting_audio['last_file_num'] = file_num
            connecting_audio['audio_data'] = None
            connecting_audio['out'] = True
            file_count += 1
    elif speech_segments:
        start = speech_segments[0]['start']

        for i in range(len(speech_segments)):
            current_start = speech_segments[i]['start']
            current_end = speech_segments[i]['end']
            margin_start = start - margin

            # if over the current chunk including the margin, or if connecting to the next chunk may have more than the threshold space
            if current_end + margin > frame_length or frame_length - current_end < threshold:
                connecting_audio = {
                    'audio_data': last_audio[margin_start * sample_width : ] + audio_data if last_audio is not None and margin_start < 0 else audio_data[max(0, start - margin) * sample_width : ],
                    'end_frame': speech_segments[-1]['end'],
                    'length_to_end': frame_length - speech_segments[-1]['end'],
                    'last_file_num': file_num,
                    'out': False
                }
                break

            next_start = speech_segments[i + 1]['start'] if i < len(speech_segments) - 1 else None

            if next_start is None or next_start - current_end >= threshold:
                output_file_path = os.path.join("result", f"audio_{file_count}.wav")
                out_oudio = last_audio[margin_start * sample_width : ] + audio_data[ : (current_end + margin) * sample_width] if last_audio is not None and margin_start < 0 else audio_data[max(0, margin_start) * sample_width : (current_end + margin) * sample_width]
                write_wav_file(output_file_path, out_oudio, sample_width)
                print(f"{output_file_path} code: 7")
                file_count += 1
                start = next_start

    last_audio = audio_data



    # if connecting_audio:
    #     output_file_path = os.path.join("result", f"audio_{file_count}.wav")
    #     await write_wav_file(output_file_path, connecting_audio['audio_data'], sample_width)
    #     print(f"{output_file_path} code: 8")

class FileHandler(FileSystemEventHandler):

    def __init__(self):
        self.previous_file = None

    def on_created(self, event):
        if not os.path.isdir(event.src_path):
            file_name = os.path.basename(event.src_path)
            if fnmatch.fnmatch(file_name, '*.wav'):
                if self.previous_file:
                    process_audio(self.previous_file)
                self.previous_file = event.src_path
                 # TODO: save last

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real-time Voice Activity Detection")
    parser.add_argument("url", help="Livestream URL")
    parser.add_argument("--chunk_size", type=int, default=3, help="Chunk size")
    parser.add_argument("--threshold", type=int, default=SAMPLING_RATE, help="Threshold for a sentence to split (frame)")
    parser.add_argument("--margin", type=int, default=SAMPLING_RATE, help="Margin to be added before and after splitting (frame)")

    args = parser.parse_args()

    url = args.url
    chunk_size = args.chunk_size
    threshold = args.threshold
    margin = args.margin

    recreate_directory("tmp")
    recreate_directory("result")

    vad_model, vad_utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                              model='silero_vad',
                              force_reload=True,
                              onnx=True)
    (get_speech_timestamps,
    save_audio,
    read_audio,
    VADIterator,
    collect_chunks) = vad_utils

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
                break
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
