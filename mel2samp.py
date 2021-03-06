# *****************************************************************************
#  Copyright (c) 2018, NVIDIA CORPORATION.  All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#      * Redistributions of source code must retain the above copyright
#        notice, this list of conditions and the following disclaimer.
#      * Redistributions in binary form must reproduce the above copyright
#        notice, this list of conditions and the following disclaimer in the
#        documentation and/or other materials provided with the distribution.
#      * Neither the name of the NVIDIA CORPORATION nor the
#        names of its contributors may be used to endorse or promote products
#        derived from this software without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
#  ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
#  WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
#  DISCLAIMED. IN NO EVENT SHALL NVIDIA CORPORATION BE LIABLE FOR ANY
#  DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
#  (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
#  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
#  ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
#  SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# *****************************************************************************\
import os
import random
import argparse
import json
import torch
import torch.utils.data
import sys
from scipy.io.wavfile import read
import binpacking
import numpy as np

# We're using the audio processing from TacoTron2 to make sure it matches
sys.path.insert(0, 'tacotron2')
from tacotron2.layers import TacotronSTFT

MAX_WAV_VALUE = 32768.0

def files_to_list(filename):
    """
    Takes a text file of filenames and makes a list of filenames
    """
    with open(filename, encoding='utf-8') as f:
        files = f.readlines()

    files = [f.rstrip() for f in files]
    return files

def load_wav_to_torch(full_path):
    """
    Loads wavdata into torch array
    """
    sampling_rate, data = read(full_path)
    return torch.from_numpy(data).float(), sampling_rate

# forward_input[0] = mel_spectrogram:  batch x n_mel_channels x frames
# forward_input[1] = audio: batch x time

class Mel2SampSplit(torch.utils.data.Dataset):
    """
    This is the main class that calculates the spectrogram and returns the
    spectrogram, audio pair.
    """
    def __init__(self, training_files, segment_length, filter_length,
                 hop_length, win_length, sampling_rate, mel_fmin, mel_fmax):
        self.audio_files = files_to_list(training_files)
        random.seed(1234)
        self.stft = TacotronSTFT(filter_length=filter_length,
                                 hop_length=hop_length,
                                 win_length=win_length,
                                 sampling_rate=sampling_rate,
                                 mel_fmin=mel_fmin, mel_fmax=mel_fmax)
        self.segment_length = segment_length
        self.sampling_rate = sampling_rate
        self.dataset = self.pack()





    def pack(self):
        timings = np.zeros(len(self.audio_files), dtype= np.int32)
        PAD = 350
        assert(self.sampling_rate % PAD == 0)

        for i,file in enumerate(self.audio_files):
            audio, sampling_rate = load_wav_to_torch(file)
            if sampling_rate != self.sampling_rate:
                raise ValueError("{} SR doesn't match target {} SR".format(
                    sampling_rate, self.sampling_rate))
            t = audio.size(0)
            t2 = t + t % PAD
            
            timings[i] = t2

        segment_len = self.sampling_rate

        total_time = timings.sum() 
        n_data = int(total_time // segment_len) if total_time % segment_len  == 0 else int((total_time // segment_len) + 1)
        
        ##import pdb; pdb.set_trace()
        
        dataset = torch.zeros([ n_data,segment_len], dtype=torch.float32 ) ## all data will be here
        offset = 0
        cur = 0
        for i,file in enumerate(self.audio_files):
            audio, _ = load_wav_to_torch(file)
            audio = torch.nn.functional.pad(audio, (0, timings[i] - audio.size(0)), 'constant').data
            assert(timings[i]  == audio.size(0))
            data_left =  audio.size(0)
            data_offset = 0
            space = segment_len - offset
            while (data_left >= space): ## fill the next data segment to the end
                dataset.data[cur,offset:offset+space] = audio[data_offset:data_offset+space]
                data_left = data_left - space
                data_offset = data_offset + space
                offset = 0
                space = segment_len
                cur = cur + 1

            ## append whats left in the next data segement
            if data_left > 0:
                new_offset = offset + data_left
                dataset.data[cur,offset:new_offset] = audio[data_offset:]
                offset = new_offset
                
        return dataset

    def get_mel(self, audio):
        audio_norm = audio / MAX_WAV_VALUE
        audio_norm = audio_norm.unsqueeze(0)
        audio_norm = torch.autograd.Variable(audio_norm, requires_grad=False)
        melspec = self.stft.mel_spectrogram(audio_norm)
        melspec = torch.squeeze(melspec, 0)
        return melspec

    def __getitem__(self, index):
        # Read audio
        audio = self.dataset.data[index,:]
        mel = self.get_mel(audio)
        audio = audio / MAX_WAV_VALUE
        return (mel, audio)

    def __len__(self):
        return self.dataset.size(0)


class Mel2Samp2(torch.utils.data.Dataset):
    """
    This is the main class that calculates the spectrogram and returns the
    spectrogram, audio pair.
    """
    def __init__(self, training_files, segment_length, filter_length,
                 hop_length, win_length, sampling_rate, mel_fmin, mel_fmax):
        self.audio_files = files_to_list(training_files)
        random.seed(1234)
        random.shuffle(self.audio_files)

        self.stft = TacotronSTFT(filter_length=filter_length,
                                 hop_length=hop_length,
                                 win_length=win_length,
                                 sampling_rate=sampling_rate,
                                 mel_fmin=mel_fmin, mel_fmax=mel_fmax)
        self.segment_length = segment_length
        self.sampling_rate = sampling_rate
        self.everything = self.pack()


        self.max_time = self.segment_length

        best = -1
        score = 0.0
        if self.max_time == 0: ##auto configuration for maximum efficiency
            for x in range(250000, 1000000,10000):
                self.max_time = x
                self.do_binpacking()

                utilized =   np.asarray(self.volumes).mean()/self.max_time
                if utilized > score:
                    score = utilized
                    best= x

        self.max_time = best
        self.do_binpacking()

        ##import pdb; pdb.set_trace()
        perm = list(range(len(self.balancer)))
        random.shuffle(perm)
        self.volumes = [self.volumes[p] for p in perm  ]
        self.balancer = [self.balancer[p] for p in perm  ]

        
    def pack(self):
        for file in self.audio_files:
            audio, sampling_rate = load_wav_to_torch(file)
            if sampling_rate != self.sampling_rate:
                raise ValueError("{} SR doesn't match target {} SR".format(
                    sampling_rate, self.sampling_rate))
            timings.append(audio.size(0))



    def get_timings(self):
        timings = np.zeros(len(self.audio_files))
        for file in self.audio_files:
            audio, sampling_rate = load_wav_to_torch(file)
            if sampling_rate != self.sampling_rate:
                raise ValueError("{} SR doesn't match target {} SR".format(
                    sampling_rate, self.sampling_rate))
            timings.append(audio.size(0))

    def get_mel(self, audio):
        audio_norm = audio / MAX_WAV_VALUE
        audio_norm = audio_norm.unsqueeze(0)
        audio_norm = torch.autograd.Variable(audio_norm, requires_grad=False)
        melspec = self.stft.mel_spectrogram(audio_norm)
        melspec = torch.squeeze(melspec, 0)
        return melspec

    def __getitem__(self, index):
        # Read audio
        print(index)

        idxs = self.balancer[index]
        time = self.volumes[index]

        pad = (self.max_time - time) // (len(idxs)- 1)
        print(pad)
        print(time)
        print(idxs)
        audios = []
        for k,idx in enumerate(idxs):
            filename = self.audio_files[idx]
            audio, sampling_rate = load_wav_to_torch(filename)
            
            if sampling_rate != self.sampling_rate:
                raise ValueError("{} SR doesn't match target {} SR".format(
                    sampling_rate, self.sampling_rate))

            print("before pad %d: %s" % ( idx ,audio.shape))

            if k != len(idxs) - 1:
                audio = torch.nn.functional.pad(audio, (0, pad), 'constant').data
                print("after pad %d: %s" % ( idx ,audio.shape))
            
            audios.append(audio)

        audio = torch.cat(audios)
        print("after cat: %s" % audio.shape)

        if audio.size(0) < self.max_time:
            audio = torch.nn.functional.pad(audio, (0, self.max_time- audio.size(0)), 'constant').data
        print("after last pad: %s" % audio.shape)

        mel = self.get_mel(audio)
        audio = audio / MAX_WAV_VALUE

        return (mel, audio)



    def __len__(self):
        return len(self.balancer)

class Mel2Samp(torch.utils.data.Dataset):
    """
    This is the main class that calculates the spectrogram and returns the
    spectrogram, audio pair.
    """
    def __init__(self, training_files, segment_length, filter_length,
                 hop_length, win_length, sampling_rate, mel_fmin, mel_fmax):
        self.audio_files = files_to_list(training_files)
        random.seed(1234)
        random.shuffle(self.audio_files)
        self.stft = TacotronSTFT(filter_length=filter_length,
                                 hop_length=hop_length,
                                 win_length=win_length,
                                 sampling_rate=sampling_rate,
                                 mel_fmin=mel_fmin, mel_fmax=mel_fmax)
        self.segment_length = segment_length
        self.sampling_rate = sampling_rate

    def get_mel(self, audio):
        audio_norm = audio / MAX_WAV_VALUE
        audio_norm = audio_norm.unsqueeze(0)
        audio_norm = torch.autograd.Variable(audio_norm, requires_grad=False)
        melspec = self.stft.mel_spectrogram(audio_norm)
        melspec = torch.squeeze(melspec, 0)
        return melspec

    def __getitem__(self, index):
        # Read audio
        filename = self.audio_files[index]
        audio, sampling_rate = load_wav_to_torch(filename)
        if sampling_rate != self.sampling_rate:
            raise ValueError("{} SR doesn't match target {} SR".format(
                sampling_rate, self.sampling_rate))

        # Take segment
        if audio.size(0) >= self.segment_length:
            max_audio_start = audio.size(0) - self.segment_length
            audio_start = random.randint(0, max_audio_start)
            audio = audio[audio_start:audio_start+self.segment_length]
        else:
            audio = torch.nn.functional.pad(audio, (0, self.segment_length - audio.size(0)), 'constant').data

        mel = self.get_mel(audio)
        audio = audio / MAX_WAV_VALUE

        return (mel, audio)

    def __len__(self):
        return len(self.audio_files)

# ===================================================================
# Takes directory of clean audio and makes directory of spectrograms
# Useful for making test sets
# ===================================================================
if __name__ == "__main__":
    # Get defaults so it can work with no Sacred
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', "--filelist_path", required=True)
    parser.add_argument('-c', '--config', type=str,
                        help='JSON file for configuration')
    parser.add_argument('-o', '--output_dir', type=str,
                        help='Output directory')
    args = parser.parse_args()

    with open(args.config) as f:
        data = f.read()
    data_config = json.loads(data)["data_config"]
    mel2samp = Mel2Samp(**data_config)

    filepaths = files_to_list(args.filelist_path)

    # Make directory if it doesn't exist
    if not os.path.isdir(args.output_dir):
        os.makedirs(args.output_dir)
        os.chmod(args.output_dir, 0o775)

    for filepath in filepaths:
        audio, sr = load_wav_to_torch(filepath)
        melspectrogram = mel2samp.get_mel(audio)
        filename = os.path.basename(filepath)
        new_filepath = args.output_dir + '/' + filename + '.pt'
        print(new_filepath)
        torch.save(melspectrogram, new_filepath)
