import argparse
import json
import os
import sys

import numpy as np
import soundfile as sf


def _emit(payload):
	sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
	sys.stdout.flush()


def _probe(path):
	info = sf.info(path)
	return {
		"path": path,
		"frames": int(info.frames),
		"sampleRate": int(info.samplerate),
		"channels": int(info.channels),
		"durationSeconds": float(info.duration),
		"durationMs": int(round(float(info.duration) * 1000.0)),
		"format": info.format,
		"subtype": info.subtype,
	}


def _mixdown_mono(audio):
	if audio.ndim == 1:
		return audio.reshape(-1, 1)
	if audio.shape[1] == 1:
		return audio
	return np.mean(audio, axis=1, keepdims=True, dtype=np.float32)


def _trim_silence(audio, threshold=0.01):
	if audio.size == 0:
		return audio
	peaks = np.max(np.abs(audio), axis=1)
	indices = np.flatnonzero(peaks > float(threshold))
	if indices.size == 0:
		return audio
	return audio[int(indices[0]): int(indices[-1]) + 1]


def _normalize_peak(audio, target_peak=0.95):
	if audio.size == 0:
		return audio
	peak = float(np.max(np.abs(audio)))
	if peak <= 0.0:
		return audio
	gain = float(target_peak) / peak
	return np.clip(audio * gain, -1.0, 1.0)


def _extract(path, out_path, start_ms, end_ms, normalize=False, trim_silence=False):
	info = sf.info(path)
	total_frames = int(info.frames)
	sample_rate = int(info.samplerate)
	start_frame = max(0, int(round((float(start_ms) / 1000.0) * sample_rate)))
	end_frame = total_frames if end_ms is None else min(total_frames, int(round((float(end_ms) / 1000.0) * sample_rate)))
	if end_frame <= start_frame:
		raise RuntimeError("End marker must be after start marker")
	audio, __ = sf.read(
		path,
		start=start_frame,
		frames=end_frame - start_frame,
		dtype="float32",
		always_2d=True,
	)
	if audio.size == 0:
		raise RuntimeError("Selected range contains no audio")
	if trim_silence:
		audio = _trim_silence(audio)
	if audio.size == 0:
		raise RuntimeError("Selected range contains only silence after trimming")
	if normalize:
		audio = _normalize_peak(audio)
	audio = _mixdown_mono(audio)
	os.makedirs(os.path.dirname(out_path), exist_ok=True)
	sf.write(out_path, audio, sample_rate, subtype="PCM_16")
	result_info = sf.info(out_path)
	return {
		"path": out_path,
		"frames": int(result_info.frames),
		"sampleRate": int(result_info.samplerate),
		"channels": int(result_info.channels),
		"durationSeconds": float(result_info.duration),
		"durationMs": int(round(float(result_info.duration) * 1000.0)),
		"format": result_info.format,
		"subtype": result_info.subtype,
		"normalized": bool(normalize),
		"trimSilence": bool(trim_silence),
	}


def main(argv=None):
	parser = argparse.ArgumentParser(prog="maxlogic_xtts_v2_audio_tools")
	subparsers = parser.add_subparsers(dest="command", required=True)

	probe_parser = subparsers.add_parser("probe")
	probe_parser.add_argument("--path", required=True)

	extract_parser = subparsers.add_parser("extract")
	extract_parser.add_argument("--path", required=True)
	extract_parser.add_argument("--out", required=True)
	extract_parser.add_argument("--start-ms", required=True, type=float)
	extract_parser.add_argument("--end-ms", required=True, type=float)
	extract_parser.add_argument("--normalize", action="store_true")
	extract_parser.add_argument("--trim-silence", action="store_true")

	args = parser.parse_args(argv)
	try:
		if args.command == "probe":
			_emit({"ok": True, "result": _probe(args.path)})
			return 0
		if args.command == "extract":
			_emit(
				{
					"ok": True,
					"result": _extract(
						args.path,
						args.out,
						start_ms=args.start_ms,
						end_ms=args.end_ms,
						normalize=args.normalize,
						trim_silence=args.trim_silence,
					),
				}
			)
			return 0
		raise RuntimeError("Unknown command: %s" % args.command)
	except Exception as error:
		_emit({"ok": False, "error": str(error)})
		return 1


if __name__ == "__main__":
	raise SystemExit(main())
