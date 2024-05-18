import torch
import torch.nn as nn
import math
from ddx7.core import *

def exp_sigmoid(x):
    return 2 * torch.sigmoid(x)**(math.log(10)) + 1e-7

def remove_above_nyquist(amplitudes, pitch, sampling_rate):
    n_harm = amplitudes.shape[-1]
    pitches = pitch * torch.arange(1, n_harm + 1).to(pitch)
    aa = (pitches < sampling_rate / 2).float() + 1e-4
    return amplitudes * aa


class FMSynth(nn.Module):
    def __init__(self,sample_rate,block_size,fr=[1,1,1,1,3,14],max_ol=2,
        scale_fn = torch.sigmoid,synth_module='fmstrings'):
        super().__init__()
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.reverb = Reverb(length=sample_rate, sample_rate=sample_rate)
        fr = torch.tensor(fr) # Frequency Ratio
        self.register_buffer("fr", fr) #Non learnable but sent to GPU if declared as buffers, and stored in model dictionary
        self.scale_fn = scale_fn
        self.use_cumsum_nd = False
        self.max_ol = max_ol

        available_synths = {'fmstrings': fm_string_synth}

        self.synth_module = available_synths[synth_module]

    def forward(self,controls):

        ol = self.max_ol*self.scale_fn(controls['ol'])
        ol_up = upsample(ol, self.block_size,'linear')
        f0_up = upsample(controls['f0_hz'], self.block_size,'linear')
        signal = self.synth_module(f0_up,
                                ol_up,
                                self.fr,
                                self.sample_rate,
                                self.max_ol,
                                self.use_cumsum_nd)
        #reverb part
        signal = self.reverb(signal)

        synth_out = {
            'synth_audio': signal,
            'ol': ol,
            'f0_hz': controls['f0_hz']
            }
        return synth_out

class Reverb(nn.Module):
    def __init__(self, length, sample_rate, initial_wet=0, initial_decay=5):
        super().__init__()
        self.length = length
        self.sample_rate = sample_rate

        self.noise = nn.Parameter((torch.rand(length) * 2 - 1).unsqueeze(-1))
        self.decay = nn.Parameter(torch.tensor(float(initial_decay)))
        self.wet = nn.Parameter(torch.tensor(float(initial_wet)))

        t = torch.arange(self.length) / self.sample_rate
        t = t.reshape(1, -1, 1)
        self.register_buffer("t", t)

    def build_impulse(self):
        t = torch.exp(-nn.functional.softplus(-self.decay) * self.t * 500)
        noise = self.noise * t
        impulse = noise * torch.sigmoid(self.wet)
        impulse[:, 0] = 1
        return impulse

    def forward(self, x):
        lenx = x.shape[1]
        impulse = self.build_impulse()
        impulse = nn.functional.pad(impulse, (0, 0, 0, lenx - self.length))

        x = fft_convolve(x.squeeze(-1), impulse.squeeze(-1)).unsqueeze(-1)

        return x
