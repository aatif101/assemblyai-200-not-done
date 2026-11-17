#!/usr/bin/env python3
"""Paced PCM streaming with server-side medical ASR, diarization and redaction."""
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

# Preserve clinical content while removing personal and identifying details.
PII_POLICIES = [
    'person_name', 'phone_number', 'us_social_security_number', 'date_of_birth',
    'person_age', 'email_address', 'location', 'account_number',
    'banking_information', 'credit_card_number', 'credit_card_expiration',
    'credit_card_cvv', 'drivers_license', 'healthcare_number', 'passport_number',
    'ip_address', 'password', 'username', 'url', 'vehicle_id',
]
PARAMETERS = {
    'sample_rate': 16000,
    'encoding': 'pcm_s16le',
    'speech_model': 'universal-streaming-english',
    'domain': 'medical-v1',
    'speaker_labels': 'true',
    'format_turns': 'true',
    'redact_pii': 'true',
    'redact_pii_policies': json.dumps(PII_POLICIES),
    'redact_pii_sub': 'entity_name',
    'include_partial_turns': 'false',
    'continuous_partials': 'false',
    'customer_support_audio_capture': 'false',
}


class TranscriptionError(Exception):
    pass


class Turns:
    def __init__(self):
        self.items = {}

    def receive(self, message):
        kind = message.get('type')
        if kind == 'Turn':
            order = message['turn_order']
            previous = self.items.get(order)
            # Formatted finals supersede the unformatted final of the same turn.
            if previous and previous.get('turn_is_formatted') and not message.get('turn_is_formatted'):
                return
            self.items[order] = message.copy()
        elif kind == 'SpeakerRevision':
            for revision in message['revisions']:
                if revision['turn_order'] in self.items:
                    self.items[revision['turn_order']]['speaker_label'] = revision.get('speaker_label')

    def result(self):
        result = []
        for _, turn in sorted(self.items.items()):
            if not turn.get('transcript'):
                continue
            if not turn.get('end_of_turn'):
                raise TranscriptionError('Stream ended with an unfinished turn.')
            # Speaker IDs are anonymous voice labels, never inferred identities.
            result.append({'speaker': turn.get('speaker_label') or 'UNKNOWN',
                           'text': turn['transcript']})
        return {'turns': result}


async def send_audio(ws, source):
    # 100 ms frames, paced by a monotonic clock; never send the WAV header.
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    while True:
        chunk = source.readframes(1600)
        if not chunk:
            break
        # The service accepts frames of at least 50 ms; pad only the last frame.
        chunk += b'\x00' * max(0, 1600 - len(chunk))
        deadline = max(deadline, loop.time()) + len(chunk) / 32000
        await asyncio.sleep(max(0, deadline - loop.time()))
        await ws.send(chunk)
    # Give the final spoken turn time to end before requesting termination.
    for _ in range(10):
        await asyncio.sleep(0.1)
        await ws.send(b'\x00' * 3200)
    await ws.send(json.dumps({'type': 'Terminate'}))


async def transcribe(source, audit, api_key):
    turns = Turns()
    uri = 'wss://streaming.assemblyai.com/v3/ws?' + urlencode(PARAMETERS)
    async with connect(uri, additional_headers={'Authorization': api_key,
                       'AssemblyAI-Version': '2025-05-12'},
                       open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024) as ws:
        async def receive(timeout=30):
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if not isinstance(raw, str):
                raise TranscriptionError('Unexpected binary response from AssemblyAI.')
            # Write the original JSON message, without reserializing or filtering it.
            audit.write(raw + '\n')
            audit.flush()
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise TranscriptionError('Unexpected response from AssemblyAI.')
            if message.get('type') in ('Error', 'Warning') or message.get('error'):
                # Never print server text, which could include sensitive data.
                raise TranscriptionError('AssemblyAI reported an error or warning; see the audit log.')
            return message

        begin = await receive()
        if begin.get('type') != 'Begin':
            raise TranscriptionError('AssemblyAI did not begin a session.')
        config = begin.get('configuration', {})
        # Verify before sending any patient audio. No fallback to unredacted ASR.
        if not (config.get('redact_pii') is True and
                config.get('speaker_labels') is True and
                config.get('domain') == 'medical-v1'):
            raise TranscriptionError('Server did not confirm medical mode, speaker labels and PII redaction.')
        sender = asyncio.create_task(send_audio(ws, source))
        try:
            while True:
                # Silence may produce no turns. Allow the remaining audio duration
                # while sending, then bound the final drain to 30 seconds.
                timeout = 30 if sender.done() else source.getnframes() / 16000 + 30
                message = await receive(timeout)
                turns.receive(message)
                if message.get('type') == 'Termination':
                    if not sender.done():
                        raise TranscriptionError('Server terminated before all audio was sent.')
                    await sender
                    return turns.result()
                if sender.done():
                    # Propagate send failures without waiting for a receive timeout.
                    await sender
        finally:
            if not sender.done():
                sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    key = os.environ.get('ASSEMBLYAI_API_KEY')
    if not key:
        raise TranscriptionError('ASSEMBLYAI_API_KEY is not set.')
    audit_path = Path(str(args.output) + '.frames.jsonl')
    if args.audio.resolve() in (args.output.resolve(), audit_path.resolve()):
        raise TranscriptionError('Output paths must differ from the input audio.')
    # Refuse to overwrite prior results or audit logs.
    if args.output.exists() or audit_path.exists():
        raise TranscriptionError('Output or audit log already exists; choose a new output path.')
    with wave.open(str(args.audio), 'rb') as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (16000, 1, 2, 'NONE'):
            raise TranscriptionError('Input must be a 16 kHz mono, 16-bit PCM WAV.')
        if not source.getnframes():
            raise TranscriptionError('Input audio is empty.')
        with audit_path.open('x', encoding='utf-8', newline='\n') as audit:
            result = asyncio.run(transcribe(source, audit, key))
        # Publish a complete transcript only after the server confirms termination.
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=args.output.parent, delete=False) as handle:
                temp = handle.name
                json.dump(result, handle, ensure_ascii=False)
                handle.write('\n')
            os.replace(temp, args.output)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
    print(f'Transcription complete: {len(result["turns"])} turns.', file=sys.stderr)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except KeyboardInterrupt:
        print('Transcription interrupted.', file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Library exceptions may contain response bodies: do not log their text.
        detail = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f'Transcription failed: {detail}', file=sys.stderr)
        sys.exit(1)
