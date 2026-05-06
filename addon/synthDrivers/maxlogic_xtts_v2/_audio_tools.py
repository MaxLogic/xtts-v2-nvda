import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

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


def _convert_to_wav(path, out_path):
	out_dir = os.path.dirname(out_path)
	if out_dir:
		os.makedirs(out_dir, exist_ok=True)
	try:
		info = sf.info(path)
		with sf.SoundFile(path, "r") as source:
			with sf.SoundFile(
				out_path,
				"w",
				samplerate=int(info.samplerate),
				channels=int(info.channels),
				format="WAV",
				subtype="PCM_16",
			) as target:
				_copy_frames(source, target, int(info.frames))
	except Exception as soundfile_error:
		ffmpeg_path = shutil.which("ffmpeg")
		if not ffmpeg_path:
			raise RuntimeError(
				"Could not decode this audio file. Install ffmpeg or convert the file to WAV first. "
				"Decoder error: %s" % soundfile_error
			)
		result = subprocess.run(
			[
				ffmpeg_path,
				"-y",
				"-hide_banner",
				"-loglevel",
				"error",
				"-i",
				path,
				"-vn",
				"-map",
				"0:a:0",
				"-c:a",
				"pcm_s16le",
				out_path,
			],
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			encoding="utf-8",
			errors="replace",
			creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
		)
		if result.returncode != 0:
			raise RuntimeError((result.stderr or result.stdout or "ffmpeg could not decode this audio file").strip())
	return _probe(out_path)


def _copy_frames(source, target, frame_count, block_size=262144):
	remaining = int(frame_count)
	while remaining > 0:
		chunk_frames = min(remaining, int(block_size))
		audio = source.read(frames=chunk_frames, always_2d=True)
		if audio.size == 0:
			break
		target.write(audio)
		remaining -= int(audio.shape[0])


def _delete_snippet(path, start_ms, end_ms):
	info = sf.info(path)
	total_frames = int(info.frames)
	sample_rate = int(info.samplerate)
	start_frame = max(0, min(total_frames, int(round((float(start_ms) / 1000.0) * sample_rate))))
	end_frame = max(0, min(total_frames, int(round((float(end_ms) / 1000.0) * sample_rate))))
	if end_frame <= start_frame:
		raise RuntimeError("End marker must be after start marker")
	directory = os.path.dirname(path) or "."
	base_name = os.path.basename(path)
	temp_fd, temp_path = tempfile.mkstemp(prefix=base_name + ".", suffix=".tmp", dir=directory)
	os.close(temp_fd)
	try:
		with sf.SoundFile(path, "r") as source:
			with sf.SoundFile(
				temp_path,
				"w",
				samplerate=sample_rate,
				channels=int(info.channels),
				format=info.format,
				subtype=info.subtype,
			) as target:
				source.seek(0)
				_copy_frames(source, target, start_frame)
				source.seek(end_frame)
				_copy_frames(source, target, total_frames - end_frame)
		os.replace(temp_path, path)
	except Exception:
		try:
			os.remove(temp_path)
		except Exception:
			pass
		raise
	result = _probe(path)
	result["deletedDurationMs"] = int(round(((end_frame - start_frame) / float(sample_rate)) * 1000.0))
	result["deletedFrames"] = int(end_frame - start_frame)
	return result


def _copy_segment(path, out_path, start_ms, end_ms):
	info = sf.info(path)
	total_frames = int(info.frames)
	sample_rate = int(info.samplerate)
	start_frame = max(0, min(total_frames, int(round((float(start_ms) / 1000.0) * sample_rate))))
	end_frame = max(0, min(total_frames, int(round((float(end_ms) / 1000.0) * sample_rate))))
	if end_frame <= start_frame:
		raise RuntimeError("End marker must be after start marker")
	out_dir = os.path.dirname(out_path)
	if out_dir:
		os.makedirs(out_dir, exist_ok=True)
	with sf.SoundFile(path, "r") as source:
		with sf.SoundFile(
			out_path,
			"w",
			samplerate=sample_rate,
			channels=int(info.channels),
			format="WAV",
			subtype="PCM_16",
		) as target:
			source.seek(start_frame)
			_copy_frames(source, target, end_frame - start_frame)
	result = _probe(out_path)
	result["copiedDurationMs"] = int(round(((end_frame - start_frame) / float(sample_rate)) * 1000.0))
	result["copiedFrames"] = int(end_frame - start_frame)
	return result


def main(argv=None):
	parser = argparse.ArgumentParser(prog="maxlogic_xtts_v2_audio_tools")
	subparsers = parser.add_subparsers(dest="command", required=True)

	probe_parser = subparsers.add_parser("probe")
	probe_parser.add_argument("--path", required=True)

	convert_parser = subparsers.add_parser("convert-to-wav")
	convert_parser.add_argument("--path", required=True)
	convert_parser.add_argument("--out", required=True)

	extract_parser = subparsers.add_parser("extract")
	extract_parser.add_argument("--path", required=True)
	extract_parser.add_argument("--out", required=True)
	extract_parser.add_argument("--start-ms", required=True, type=float)
	extract_parser.add_argument("--end-ms", required=True, type=float)
	extract_parser.add_argument("--normalize", action="store_true")
	extract_parser.add_argument("--trim-silence", action="store_true")

	delete_parser = subparsers.add_parser("delete-snippet")
	delete_parser.add_argument("--path", required=True)
	delete_parser.add_argument("--start-ms", required=True, type=float)
	delete_parser.add_argument("--end-ms", required=True, type=float)

	copy_parser = subparsers.add_parser("copy-segment")
	copy_parser.add_argument("--path", required=True)
	copy_parser.add_argument("--out", required=True)
	copy_parser.add_argument("--start-ms", required=True, type=float)
	copy_parser.add_argument("--end-ms", required=True, type=float)

	args = parser.parse_args(argv)
	try:
		if args.command == "probe":
			_emit({"ok": True, "result": _probe(args.path)})
			return 0
		if args.command == "convert-to-wav":
			_emit({"ok": True, "result": _convert_to_wav(args.path, args.out)})
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
		if args.command == "delete-snippet":
			_emit(
				{
					"ok": True,
					"result": _delete_snippet(
						args.path,
						start_ms=args.start_ms,
						end_ms=args.end_ms,
					),
				}
			)
			return 0
		if args.command == "copy-segment":
			_emit(
				{
					"ok": True,
					"result": _copy_segment(
						args.path,
						args.out,
						start_ms=args.start_ms,
						end_ms=args.end_ms,
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
