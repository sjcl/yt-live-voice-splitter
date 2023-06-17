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
import torch
import wave
from faster_whisper import WhisperModel


SAMPLING_RATE = 16000
THRESHOLD = SAMPLING_RATE
MARGIN = SAMPLING_RATE

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

async def read_wav_file(file_path):
    with wave.open(file_path, 'rb') as wav_file:
        num_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        num_frames = wav_file.getnframes()
        audio_data = wav_file.readframes(num_frames)
    return audio_data, num_channels, sample_width, num_frames

async def write_wav_file(file_path, audio_data, sample_width):
    with wave.open(file_path, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(SAMPLING_RATE)
        wav_file.writeframes(audio_data)

async def process_audio(url, duration):
    if os.path.exists("tmp"):
        shutil.rmtree("tmp")
    os.makedirs("tmp", exist_ok=True)

    if os.path.exists("result"):
        shutil.rmtree("result")
    os.makedirs("result", exist_ok=True)

    vad_model, vad_utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                              model='silero_vad',
                              force_reload=True,
                              onnx=True)
    (get_speech_timestamps,
    save_audio,
    read_audio,
    VADIterator,
    collect_chunks) = vad_utils

    whisper_model = WhisperModel("large-v2", device="cuda", compute_type="float16")

    audio_url = get_audio_url(url)

    split_command = f"ffmpeg -i {audio_url} -f segment -segment_time {duration} -ac 1 -ar {SAMPLING_RATE} -vn tmp/audio_%03d.wav"
    devnull = open('/dev/null', 'w')
    # process = await asyncio.create_subprocess_shell(split_command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    process = await asyncio.create_subprocess_shell(split_command, stdout=devnull, stderr=devnull)

    await asyncio.sleep(duration)

    prev_files = []
    connecting_audio = None
    last_audio = None
    file_count = 1
    while True:
        try:
            current_files = os.listdir("tmp")
            if len(current_files) >= 1:
                current_files.pop()
            new_files = [
                f for f in current_files if f not in prev_files and f.endswith(".wav")
            ]

            if new_files:
                for new_file in new_files:
                    file_num = int(new_file.split("_")[1].split(".")[0])
                    audio_path = os.path.join("tmp", new_file)

                    audio = read_audio(audio_path, sampling_rate=SAMPLING_RATE)
                    speech_segments = get_speech_timestamps(audio, vad_model, sampling_rate=SAMPLING_RATE)
                    print(f"{audio_path} {speech_segments}")
                    
                    audio_data, num_channels, sample_width, frame_length = await read_wav_file(audio_path)

                    if connecting_audio and not connecting_audio['out']:
                        if speech_segments:
                            start = speech_segments[0]['start']

                            for i in range(len(speech_segments)):
                                current_start = speech_segments[i]['start']
                                current_end = speech_segments[i]['end']

                                if i == 0 and connecting_audio['length_to_end'] + current_start >= THRESHOLD:
                                    output_file_path = os.path.join("result", f"audio_{file_count}.wav")

                                    if connecting_audio['length_to_end'] >= MARGIN:
                                        out_audio = connecting_audio['audio_data'][ : (connecting_audio['end_frame'] + MARGIN) * sample_width]
                                        await write_wav_file(output_file_path, out_audio, sample_width)
                                    else:
                                        out_audio = connecting_audio['audio_data'] + audio_data[ : (MARGIN - connecting_audio['length_to_end']) * sample_width]
                                        await write_wav_file(output_file_path, out_audio, sample_width)
                                        connecting_audio['end_frame'] = connecting_audio['length_to_end']
                                        connecting_audio['length_to_end'] = frame_length - connecting_audio['length_to_end']
                                        connecting_audio['last_file_num'] = file_num
                                    connecting_audio['audio_data'] = None
                                    connecting_audio['out'] = True
                                    file_count += 1
                                else:
                                    if not connecting_audio['out']:
                                        if current_end + MARGIN > frame_length or frame_length - current_end < THRESHOLD:
                                            if connecting_audio['last_file_num'] < file_num:
                                                connecting_audio['audio_data'] = connecting_audio['audio_data'] + audio_data
                                                connecting_audio['last_file_num'] = file_num
                                            elif connecting_audio['end_frame'] + 1 < frame_length:
                                                connecting_audio['audio_data'] = connecting_audio['audio_data'] + audio_data[(connecting_audio['end_frame'] + 1) * sample_width : ]
                                            connecting_audio['end_frame'] = speech_segments[-1]['end']
                                            connecting_audio['length_to_end'] = frame_length - speech_segments[-1]['end']
                                            break

                                        next_start = speech_segments[i + 1]['start'] if i < len(speech_segments) - 1 else None

                                        if next_start is None or next_start - current_end >= THRESHOLD:
                                            out_audio = connecting_audio['audio_data'] + audio_data[ : (current_end + MARGIN) * sample_width] if connecting_audio['last_file_num'] < file_num else connecting_audio['audio_data'] + audio_data[(connecting_audio['end_frame'] + 1) * sample_width : (current_end + MARGIN) * sample_width]
                                            output_file_path = os.path.join("result", f"audio_{file_count}.wav")
                                            await write_wav_file(output_file_path, out_audio, sample_width)
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
                                        if current_end + MARGIN > frame_length or frame_length - current_end < THRESHOLD:
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

                                        margin_start = start - MARGIN
                                        next_start = speech_segments[i + 1]['start'] if i < len(speech_segments) - 1 else None
                                        
                                        if next_start is None or next_start - current_end >= THRESHOLD:
                                            output_file_path = os.path.join("result", f"audio_{file_count}.wav")
                                            out_oudio = last_audio[margin_start * sample_width : ] + audio_data[ : (current_end + MARGIN) * sample_width] if last_audio is not None and margin_start < 0 else audio_data[max(0, margin_start) * sample_width : (current_end + MARGIN) * sample_width]
                                            await write_wav_file(output_file_path, out_oudio, sample_width)
                                            file_count += 1
                                            start = next_start
                        else:
                            output_file_path = os.path.join("result", f"audio_{file_count}.wav")
                            if connecting_audio['length_to_end'] >= MARGIN:
                                out_audio = connecting_audio['audio_data'][ : (connecting_audio['end_frame'] + MARGIN) * sample_width]
                                await write_wav_file(output_file_path, out_audio, sample_width)
                            else:
                                out_audio =connecting_audio['audio_data'] + audio_data[ : (MARGIN - connecting_audio['length_to_end']) * sample_width]
                                await write_wav_file(output_file_path, out_audio, sample_width)
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
                            margin_start = start - MARGIN

                            # if over the current chunk including the margin, or if connecting to the next chunk may have more than the threshold space
                            if current_end + MARGIN > frame_length or frame_length - current_end < THRESHOLD:
                                connecting_audio = {
                                    'audio_data': last_audio[margin_start * sample_width : ] + audio_data if last_audio is not None and margin_start < 0 else audio_data[max(0, start - MARGIN) * sample_width : ],
                                    'end_frame': speech_segments[-1]['end'],
                                    'length_to_end': frame_length - speech_segments[-1]['end'],
                                    'last_file_num': file_num,
                                    'out': False
                                }
                                break

                            next_start = speech_segments[i + 1]['start'] if i < len(speech_segments) - 1 else None

                            if next_start is None or next_start - current_end >= THRESHOLD:
                                output_file_path = os.path.join("result", f"audio_{file_count}.wav")
                                out_oudio = last_audio[margin_start * sample_width : ] + audio_data[ : (current_end + MARGIN) * sample_width] if last_audio is not None and margin_start < 0 else audio_data[max(0, margin_start) * sample_width : (current_end + MARGIN) * sample_width]
                                await write_wav_file(output_file_path, out_oudio, sample_width)
                                file_count += 1
                                start = next_start

                    last_audio = audio_data

                prev_files = current_files
        except EOFError:
            pass
        except Exception as e:
            traceback.print_exc()
            process.kill()
            devnull.close()
            sys.exit(1)

        await asyncio.sleep(duration)

    if connecting_audio:
        output_file_path = os.path.join("result", f"audio_{file_count}.wav")
        await write_wav_file(output_file_path, connecting_audio['audio_data'], sample_width)

    devnull.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real-time Voice Activity Detection")
    parser.add_argument("url", help="Livestream URL")
    parser.add_argument("--duration", type=int, default=3, help="Chunk size")

    args = parser.parse_args()

    url = args.url
    duration = args.duration

    loop = asyncio.get_event_loop()
    loop.run_until_complete(process_audio(url, duration))
