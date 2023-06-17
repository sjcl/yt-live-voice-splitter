import torch
import os
import wave
import logging

class Splitter:

    def __init__(self, sampling_rate: int, threshold: int, margin: int):
        self.sampling_rate = sampling_rate
        self.threshold = threshold
        self.margin = margin

        self.sample_width = None
        self.connecting_audio = None
        self.last_audio = None
        self.file_count = 1

        self.vad_model, vad_utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                              model='silero_vad',
                              force_reload=True,
                              onnx=True)
        (self.get_speech_timestamps,
        self.save_audio,
        self.read_audio,
        self.VADIterator,
        self.collect_chunks) = vad_utils

    def read_wav_file(self, file_path):
        with wave.open(file_path, 'rb') as wav_file:
            num_channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            num_frames = wav_file.getnframes()
            audio_data = wav_file.readframes(num_frames)
        return audio_data, num_channels, sample_width, num_frames

    def write_wav_file(self, file_path, audio_data, sample_width):
        with wave.open(file_path, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(self.sampling_rate)
            wav_file.writeframes(audio_data)

    def process_audio(self, audio_path):
        file_num = int(os.path.basename(audio_path).split("_")[1].split(".")[0])

        audio = self.read_audio(audio_path, sampling_rate=self.sampling_rate)
        speech_segments = self.get_speech_timestamps(audio, self.vad_model, sampling_rate=self.sampling_rate)
        
        audio_data, num_channels, self.sample_width, frame_length = self.read_wav_file(audio_path)

        logging.debug(f"Processing: {audio_path} length: {frame_length} Segments: {speech_segments}")

        if self.connecting_audio and self.connecting_audio['out'] is False:
            if speech_segments:
                start = speech_segments[0]['start']

                for i in range(len(speech_segments)):
                    current_start = speech_segments[i]['start']
                    current_end = speech_segments[i]['end']

                    if i == 0 and self.connecting_audio['length_to_end'] + current_start >= self.threshold:
                        output_file_path = os.path.join("result", f"audio_{self.file_count}.wav")

                        if self.connecting_audio['length_to_end'] >= self.margin:
                            out_audio = self.connecting_audio['audio_data'][ : (self.connecting_audio['end_frame'] + self.margin) * self.sample_width]
                            self.write_wav_file(output_file_path, out_audio, self.sample_width)
                            logging.debug(f"{output_file_path} code: 1 current_start: {current_start} current_end: {current_end}")
                        else:
                            out_audio = self.connecting_audio['audio_data'] + audio_data[ : (self.margin - self.connecting_audio['length_to_end']) * self.sample_width]
                            self.write_wav_file(output_file_path, out_audio, self.sample_width)
                            logging.debug(f"{output_file_path} code: 2 current_start: {current_start} current_end: {current_end}")
                            self.connecting_audio['end_frame'] = self.connecting_audio['length_to_end']
                            self.connecting_audio['length_to_end'] = frame_length - self.connecting_audio['length_to_end']
                            self.connecting_audio['last_file_num'] = file_num
                        self.connecting_audio['audio_data'] = None
                        self.connecting_audio['out'] = True
                        self.file_count += 1
                        
                    if self.connecting_audio['out'] is False:
                        if current_end + self.margin > frame_length or frame_length - current_end < self.threshold:
                            if self.connecting_audio['last_file_num'] < file_num:
                                self.connecting_audio['audio_data'] = self.connecting_audio['audio_data'] + audio_data
                                self.connecting_audio['last_file_num'] = file_num
                            elif self.connecting_audio['end_frame'] + 1 < frame_length:
                                self.connecting_audio['audio_data'] = self.connecting_audio['audio_data'] + audio_data[(self.connecting_audio['end_frame'] + 1) * self.sample_width : ]
                            self.connecting_audio['end_frame'] = speech_segments[-1]['end']
                            self.connecting_audio['length_to_end'] = frame_length - speech_segments[-1]['end']
                            break

                        next_start = speech_segments[i + 1]['start'] if i < len(speech_segments) - 1 else None

                        if next_start is None or next_start - current_end >= self.threshold:
                            out_audio = self.connecting_audio['audio_data']
                            if self.connecting_audio['last_file_num'] < file_num:
                                out_audio += audio_data[:(current_end + self.margin) * self.sample_width]
                            else:
                                out_audio += audio_data[(self.connecting_audio['end_frame'] + 1) * self.sample_width:(current_end + self.margin) * self.sample_width]
                            output_file_path = os.path.join("result", f"audio_{self.file_count}.wav")
                            self.write_wav_file(output_file_path, out_audio, self.sample_width)
                            logging.debug(f"{output_file_path} code: 3 current_start: {current_start} current_end: {current_end}")
                            self.connecting_audio['audio_data'] = None
                            self.connecting_audio['end_frame'] = current_end
                            self.connecting_audio['length_to_end'] = frame_length - current_end
                            self.connecting_audio['last_file_num'] = file_num
                            self.connecting_audio['out'] = True
                            self.file_count += 1
                        else:
                            if self.connecting_audio['last_file_num'] < file_num:
                                self.connecting_audio['audio_data'] = self.connecting_audio['audio_data'] + audio_data[ : current_end * self.sample_width]
                                self.connecting_audio['last_file_num'] = file_num
                            else:
                                self.connecting_audio['audio_data'] = self.connecting_audio['audio_data'] + audio_data[(self.connecting_audio['end_frame'] + 1) * self.sample_width : current_end * self.sample_width]
                            self.connecting_audio['end_frame'] = current_end
                            self.connecting_audio['length_to_end'] = frame_length - current_end
                    else:
                        if current_end + self.margin > frame_length or frame_length - current_end < self.threshold:
                            if self.connecting_audio['last_file_num'] < file_num:
                                self.connecting_audio['audio_data'] = audio_data
                            else:
                                if self.connecting_audio['end_frame'] + 1 < frame_length:
                                    self.connecting_audio['audio_data'] = audio_data[(self.connecting_audio['end_frame'] + 1) * self.sample_width : ]
                                else:
                                    continue
                            self.connecting_audio['end_frame'] = current_end
                            self.connecting_audio['length_to_end'] = frame_length - current_end
                            self.connecting_audio['last_file_num'] = file_num
                            self.connecting_audio['out'] = False
                            break

                        self.margin_start = start - self.margin
                        next_start = speech_segments[i + 1]['start'] if i < len(speech_segments) - 1 else None
                        
                        if next_start is None or next_start - current_end >= self.threshold:
                            output_file_path = os.path.join("result", f"audio_{self.file_count}.wav")
                            out_oudio = self.last_audio[self.margin_start * self.sample_width : ] + audio_data[ : (current_end + self.margin) * self.sample_width] if self.last_audio is not None and self.margin_start < 0 else audio_data[max(0, self.margin_start) * self.sample_width : (current_end + self.margin) * self.sample_width]
                            self.write_wav_file(output_file_path, out_oudio, self.sample_width)
                            logging.debug(f"{output_file_path} code: 4 current_start: {current_start} current_end: {current_end}")
                            self.file_count += 1
                            start = next_start
            else:
                output_file_path = os.path.join("result", f"audio_{self.file_count}.wav")
                if self.connecting_audio['length_to_end'] >= self.margin:
                    out_audio = self.connecting_audio['audio_data'][ : (self.connecting_audio['end_frame'] + self.margin) * self.sample_width]
                    self.write_wav_file(output_file_path, out_audio, self.sample_width)
                    logging.debug(f"{output_file_path} code: 5")
                else:
                    out_audio =self.connecting_audio['audio_data'] + audio_data[ : (self.margin - self.connecting_audio['length_to_end']) * self.sample_width]
                    self.write_wav_file(output_file_path, out_audio, self.sample_width)
                    logging.debug(f"{output_file_path} code: 6")
                    self.connecting_audio['end_frame'] = self.connecting_audio['length_to_end']
                    self.connecting_audio['length_to_end'] = frame_length - self.connecting_audio['length_to_end']
                    self.connecting_audio['last_file_num'] = file_num
                self.connecting_audio['audio_data'] = None
                self.connecting_audio['out'] = True
                self.file_count += 1
        elif speech_segments:
            start = speech_segments[0]['start']

            for i in range(len(speech_segments)):
                current_start = speech_segments[i]['start']
                current_end = speech_segments[i]['end']
                self.margin_start = start - self.margin

                # if over the current chunk including the self.margin, or if connecting to the next chunk may have more than the self.threshold space
                if current_end + self.margin > frame_length or frame_length - current_end < self.threshold:
                    self.connecting_audio = {
                        'audio_data': self.last_audio[self.margin_start * self.sample_width : ] + audio_data if self.last_audio is not None and self.margin_start < 0 else audio_data[max(0, self.margin_start) * self.sample_width : ],
                        'end_frame': speech_segments[-1]['end'],
                        'length_to_end': frame_length - speech_segments[-1]['end'],
                        'last_file_num': file_num,
                        'out': False
                    }
                    break

                next_start = speech_segments[i + 1]['start'] if i < len(speech_segments) - 1 else None

                if next_start is None or next_start - current_end >= self.threshold:
                    output_file_path = os.path.join("result", f"audio_{self.file_count}.wav")
                    if self.last_audio is not None and self.margin_start < 0:
                        out_audio = self.last_audio[self.margin_start * self.sample_width : ] + audio_data[ : (current_end + self.margin) * self.sample_width]
                        self.write_wav_file(output_file_path, out_audio, self.sample_width)
                        logging.debug(f"{output_file_path} code: 7 current_start: {current_start} current_end: {current_end}")
                    else:
                        out_audio = audio_data[max(0, self.margin_start) * self.sample_width : (current_end + self.margin) * self.sample_width]
                        self.write_wav_file(output_file_path, out_audio, self.sample_width)
                        logging.debug(f"{output_file_path} code: 8 current_start: {current_start} current_end: {current_end}")
                    self.file_count += 1
                    start = next_start

        self.last_audio = audio_data

    def on_exit(self):
        if self.connecting_audio and self.connecting_audio['out'] is False:
            output_file_path = os.path.join("result", f"audio_{self.file_count}.wav")
            self.write_wav_file(output_file_path, self.connecting_audio['audio_data'], self.sample_width)
            logging.debug(f"{output_file_path} code: 9")
