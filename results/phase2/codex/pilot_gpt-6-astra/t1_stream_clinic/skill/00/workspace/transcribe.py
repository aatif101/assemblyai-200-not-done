#!/usr/bin/env python3
"""Replay PCM WAV audio to AssemblyAI v3 without persisting unredacted partials."""
import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlencode
import wave

from websockets.asyncio.client import connect

MODEL = 'universal-3-6-pro'
PARAMS = {
    'speech_model': MODEL,
    'sample_rate': 16000,
    'encoding': 'pcm_s16le',
    'domain': 'medical-v1',
    'speaker_labels': 'true',
    'speaker_labels_revision_interval_ms': 300000,
    'redact_pii': 'true',
    # Omit policies to use ALL supported PII policies.
    'redact_pii_sub': 'entity_name',
    'include_partial_turns': 'false',
    # Explicit silence settings still apply with diarization enabled.
    'min_turn_silence': 640,
    'max_turn_silence': 2560,
}


class SessionError(Exception):
    pass


def check_begin(message):
    config = message.get('configuration', {})
    expected = {'model': MODEL, 'domain': 'medical-v1',
                'speaker_labels': True, 'redact_pii': True}
    if any(config.get(key) != value for key, value in expected.items()):
        raise SessionError('Server did not confirm required medical, speaker, and redaction settings.')


class Turns:
    def __init__(self):
        self.turns = {}

    def receive(self, message):
        if message['type'] == 'Turn':
            if message.get('end_of_turn') is not True:
                raise SessionError('Server unexpectedly emitted a partial turn.')
            order = message['turn_order']
            self.turns[order] = {
                'speaker': message.get('speaker_label') or 'UNKNOWN',
                'text': message['transcript'],
            }
        elif message['type'] == 'SpeakerRevision':
            for revision in message['revisions']:
                order = revision['turn_order']
                if order not in self.turns:
                    raise SessionError('Speaker revision references a missing turn.')
                self.turns[order]['speaker'] = revision.get('speaker_label') or 'UNKNOWN'

    def result(self):
        return {'turns': [self.turns[k] for k in sorted(self.turns)]}


async def send_audio(ws, wav):
    # 800 samples = 50 ms. Pad only the final short chunk with silence.
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    while True:
        chunk = wav.readframes(800)
        if not chunk:
            break
        chunk = chunk.ljust(1600, b'\0')
        # Never catch up in a burst after a delayed send.
        deadline = max(deadline, loop.time()) + 0.05
        await asyncio.sleep(max(0, deadline - loop.time()))
        await ws.send(chunk)
    await ws.send(json.dumps({'type': 'ForceEndpoint'}))
    await ws.send(json.dumps({'type': 'Terminate'}))


async def session(wav, audit, api_key):
    turns = Turns()
    async with connect(
        'wss://streaming.assemblyai.com/v3/ws?' + urlencode(PARAMS),
        additional_headers={'Authorization': api_key},
        open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024,
    ) as ws:
        async def receive():
            raw = await ws.recv()
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8')
            # Preserve the received JSON payload exactly, including unknown fields.
            audit.write(raw + '\n')
            audit.flush()
            message = json.loads(raw)
            if message.get('type') == 'Error' or 'error' in message:
                # Never echo a server message that might contain personal data.
                raise SessionError('AssemblyAI reported a session error; inspect the audit log securely.')
            return message

        first = await asyncio.wait_for(receive(), timeout=30)
        if first.get('type') != 'Begin':
            raise SessionError('Expected AssemblyAI Begin message.')
        check_begin(first)
        sender = asyncio.create_task(send_audio(ws, wav))

        async def collect():
            while True:
                message = await receive()
                if message.get('type') == 'Termination':
                    if not sender.done():
                        raise SessionError('Session terminated before all audio was sent.')
                    return
                turns.receive(message)

        receiver = asyncio.create_task(collect())
        try:
            done, _ = await asyncio.wait(
                (sender, receiver), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            await asyncio.wait_for(receiver, timeout=30)
        finally:
            for task in (sender, receiver):
                task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return turns.result()


def atomic_write(path, result):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(result, out, ensure_ascii=False)
            out.write('\n')
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        key = os.environ.get('ASSEMBLYAI_API_KEY')
        if not key:
            raise SessionError('Set ASSEMBLYAI_API_KEY in your environment.')
        audit_path = Path(str(args.output) + '.frames.jsonl')
        if args.audio.resolve() in (args.output.resolve(), audit_path.resolve()):
            raise SessionError('Output paths must differ from the input audio.')
        with wave.open(str(args.audio), 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, 'NONE'):
                raise SessionError('Input must be a 16 kHz mono, 16-bit PCM WAV.')
            if wav.getnframes() == 0:
                raise SessionError('Input audio is empty.')
            with audit_path.open('w', encoding='utf-8', newline='') as audit:
                result = asyncio.run(session(wav, audit, key))
            atomic_write(args.output, result)
        return 0
    except SessionError as exc:
        print(str(exc), file=sys.stderr)
    except KeyboardInterrupt:
        print('Transcription interrupted.', file=sys.stderr)
    except Exception as exc:
        # Exceptions from network libraries can include payloads or credentials.
        print(f'Transcription failed ({type(exc).__name__}); no new final transcript written.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
