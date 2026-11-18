import wave


def trim_wav(source, destination, start, end):
    if not 0 <= start < end <= 1:
        raise ValueError("Select a non-empty audio region.")

    with wave.open(source, "rb") as audio:
        if audio.getcomptype() != "NONE":
            raise ValueError("Only uncompressed PCM WAV is supported.")

        params = audio.getparams()
        total_frames = audio.getnframes()

        first = int(start * total_frames)
        last = min(total_frames, int(end * total_frames))

        if last <= first:
            raise ValueError("Selected region is too short.")

        audio.setpos(first)
        frames = audio.readframes(last - first)

    with wave.open(destination, "wb") as output:
        output.setparams(params)
        output.writeframes(frames)



def copy_region_frames(source, destination, first, last):
    """Copy a half-open interval at exact PCM frame boundaries."""
    with wave.open(str(source), "rb") as audio:
        if not 0 <= first < last <= audio.getnframes():
            raise ValueError("Select a non-empty audio region.")
        audio.setpos(first)
        with wave.open(str(destination), "wb") as output:
            output.setparams(audio.getparams())
            output.setnframes(last - first)
            while first < last:
                count = min(last - first, 65536)
                data = audio.readframes(count)
                if len(data) != count * audio.getnchannels() * audio.getsampwidth():
                    raise ValueError("The source WAV is incomplete.")
                output.writeframesraw(data)
                first += count


def normalize_wav(source, destination):
    import audioop

    with wave.open(source, "rb") as audio:
        if audio.getcomptype() != "NONE":
            raise ValueError("Only uncompressed PCM WAV is supported.")

        params = audio.getparams()
        width = audio.getsampwidth()

        if width not in (1, 2, 3, 4):
            raise ValueError("Unsupported sample width.")

        frames = audio.readframes(audio.getnframes())

    if not frames:
        raise ValueError("Audio file is empty.")

    peak = audioop.max(frames, width)

    if peak == 0:
        raise ValueError("Cannot normalize silence.")

    maximum = (1 << (width * 8 - 1)) - 1
    target = maximum * (10 ** (-1.0 / 20.0))
    gain = target / peak

    normalized = audioop.mul(frames, width, gain)

    with wave.open(destination, "wb") as output:
        output.setparams(params)
        output.writeframes(normalized)


def cut_region(source, destination, start, end, keep=False):
    """Copy PCM frames, removing or keeping the selected interval."""
    if not 0 <= start < end <= 1:
        raise ValueError('Select an audio region first.')
    with wave.open(str(source), 'rb') as audio:
        if audio.getcomptype() != 'NONE':
            raise ValueError('Only PCM WAV is supported.')
        params = audio.getparams()
        raw = audio.readframes(params.nframes)
    first, last = int(start * params.nframes), int(end * params.nframes)
    if last <= first:
        raise ValueError('Selection is too short.')
    stride = params.nchannels * params.sampwidth
    result = raw[first * stride:last * stride] if keep else raw[:first * stride] + raw[last * stride:]
    if not result:
        raise ValueError('Cannot remove the entire sample.')
    with wave.open(str(destination), 'wb') as output:
        output.setparams(params)
        output.writeframes(result)
