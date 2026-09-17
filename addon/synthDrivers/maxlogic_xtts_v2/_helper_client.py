import base64
import json
import os
import re
import subprocess
import sys
import threading
import time

try:
	from ._log import get_helper_log_path
except ImportError:
	sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
	from _log import get_helper_log_path


class HelperRequestInterrupted(RuntimeError):
	pass


class HelperEngineClient(object):
	sample_rate = 24000

	def __init__(self, package_root, logger, helper_mode="synth", skip_prewarm=False):
		self.package_root = package_root
		self.logger = logger
		self.helper_mode = helper_mode
		self._request_id = 0
		self._io_lock = threading.RLock()
		# cancel() writes while another thread holds _io_lock to read a response.
		self._write_lock = threading.Lock()
		self._state_lock = threading.Lock()
		self._voice = None
		self._voices = []
		self._providers = []
		self._streaming = False
		self._process = None
		self._stderr_thread = None
		self._mode = helper_mode
		self._request_active = False
		self._request_started_at = None
		self._request_interrupted = False
		self._skip_next_prewarm = bool(skip_prewarm)
		self._closed = False
		self._start(skip_prewarm=bool(skip_prewarm))

	@classmethod
	def should_try(cls, package_root):
		if os.environ.get("MAXLOGIC_XTTS_V2_HELPER_PYTHON"):
			return True
		if os.environ.get("MAXLOGIC_XTTS_V2_FORCE_HELPER") == "1":
			return True
		return os.path.isfile(cls._repo_helper_python(package_root))

	@classmethod
	def _repo_root(cls, package_root):
		candidate = os.path.abspath(package_root)
		manifest_candidate = None
		for _ in range(5):
			candidate_name = os.path.basename(candidate).strip().lower()
			if candidate_name != "addon" and os.path.isfile(os.path.join(candidate, ".helper-venv", "Scripts", "python.exe")):
				return candidate
			if manifest_candidate is None and os.path.isfile(os.path.join(candidate, "manifest.ini")):
				manifest_candidate = candidate
			parent = os.path.dirname(candidate)
			if parent == candidate:
				break
			candidate = parent
		if manifest_candidate is not None:
			return manifest_candidate
		return os.path.abspath(os.path.join(package_root, "..", ".."))

	@classmethod
	def _repo_helper_python(cls, package_root):
		return os.path.join(cls._repo_root(package_root), ".helper-venv", "Scripts", "python.exe")

	@classmethod
	def _candidate_commands(cls, package_root):
		helper_script = os.path.join(package_root, "_helper_process.py")
		repo_python = cls._repo_helper_python(package_root)
		env_python = os.environ.get("MAXLOGIC_XTTS_V2_HELPER_PYTHON")
		candidates = []
		if env_python:
			candidates.append([env_python, helper_script])
		if os.path.isfile(repo_python):
			candidates.append([repo_python, helper_script])
		candidates.append(["py", "-3", helper_script])
		candidates.append(["py", helper_script])
		return candidates

	def _build_launch_env(self, skip_prewarm=False):
		env = os.environ.copy()
		env["MAXLOGIC_XTTS_V2_HELPER_MODE"] = self.helper_mode
		env.setdefault("COQUI_TOS_AGREED", "1")
		if skip_prewarm:
			env["MAXLOGIC_XTTS_V2_SKIP_PREWARM"] = "1"
		else:
			env.pop("MAXLOGIC_XTTS_V2_SKIP_PREWARM", None)
		return env

	def _start(self, skip_prewarm=False):
		with self._io_lock:
			self._start_locked(skip_prewarm=skip_prewarm)

	def _start_locked(self, skip_prewarm=False):
		last_error = None
		for command in self._candidate_commands(self.package_root):
			try:
				self.logger.info("Starting MaxLogic XTTS v2 helper with command %s", command)
				self._process = subprocess.Popen(
					command,
					stdin=subprocess.PIPE,
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE,
					text=True,
					encoding="utf-8",
					errors="replace",
					bufsize=1,
					creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
					env=self._build_launch_env(skip_prewarm=skip_prewarm),
				)
				self._stderr_thread = threading.Thread(target=self._drain_stderr, args=(self._process,), daemon=True)
				self._stderr_thread.start()
				ready = self._read_message()
				if ready.get("ok"):
					self._voices = ready.get("voices", [])
					self._voice = ready.get("current_voice")
					self._providers = ready.get("providers", [])
					self._streaming = bool(ready.get("streaming"))
					self.sample_rate = ready.get("sample_rate", self.sample_rate)
					self.logger.info(
						"Using MaxLogic XTTS v2 helper pid=%s with runtime %s, voices=%s, streaming=%s, helperLog=%s",
						self._process.pid,
						self._providers,
						len(self._voices),
						self._streaming,
						get_helper_log_path(),
					)
					return
				last_error = RuntimeError(ready.get("error", "helper start failed"))
				self.logger.warning("MaxLogic XTTS v2 helper start failed for command %s: %s", command, last_error)
				self._stop_process()
			except Exception as error:
				last_error = error
				self.logger.warning("MaxLogic XTTS v2 helper launch failed for command %s: %s", command, error)
				self._stop_process()
		raise RuntimeError("Unable to start MaxLogic XTTS v2 helper: %s" % last_error)

	def _consume_skip_prewarm_locked(self):
		with self._state_lock:
			value = self._skip_next_prewarm
			self._skip_next_prewarm = False
			return value

	def _ensure_running_locked(self):
		if self._closed:
			# A thread that was still queued when the owner closed us must not start an orphan.
			raise RuntimeError("MaxLogic XTTS v2 helper client is closed")
		if self._process is not None and self._process.poll() is None:
			return
		self._process = None
		self._start_locked(skip_prewarm=self._consume_skip_prewarm_locked())

	def _drain_stderr(self, process):
		if process is None or process.stderr is None:
			return
		for line in process.stderr:
			line = line.rstrip()
			if line:
				self.logger.debug("XTTS helper: %s", line)

	def _read_message(self):
		while True:
			line = self._process.stdout.readline()
			if not line:
				raise RuntimeError("Helper process exited before responding")
			line = line.strip()
			if not line:
				continue
			try:
				return json.loads(line)
			except Exception:
				self.logger.warning("Ignored non-JSON helper stdout line: %r", line[:200])

	def _send_line(self, process, payload):
		with self._write_lock:
			process.stdin.write(json.dumps(payload) + "\n")
			process.stdin.flush()

	def _request(self, payload):
		with self._io_lock:
			self._ensure_running_locked()
			self._request_id += 1
			payload = dict(payload)
			payload["id"] = self._request_id
			start_time = time.perf_counter()
			with self._state_lock:
				self._request_active = True
				self._request_started_at = start_time
				self._request_interrupted = False
			try:
				self._send_line(self._process, payload)
				response = self._read_message()
				if not response.get("ok"):
					self.logger.warning("XTTS helper request failed. op=%s error=%s", payload.get("op"), response.get("error"))
					raise RuntimeError(response.get("error", "Unknown helper error"))
				return response
			except Exception:
				with self._state_lock:
					interrupted = self._request_interrupted
				if interrupted:
					raise HelperRequestInterrupted("Helper request interrupted")
				raise
			finally:
				elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
				if payload.get("op") == "synthesize":
					self.logger.info(
						"XTTS helper response. chars=%s voice=%s elapsedMs=%s",
						len(payload.get("text", "")),
						payload.get("voice") or self._voice,
						elapsed_ms,
					)
				with self._state_lock:
					self._request_active = False
					self._request_started_at = None
					self._request_interrupted = False

	def list_voices(self):
		return list(self._voices)

	def set_voice(self, voice_name):
		self._request({"op": "set_voice", "voice": voice_name})
		self._voice = voice_name

	@property
	def current_voice(self):
		return self._voice

	def reload_voices(self, preferred_voice=None):
		response = self._request({"op": "reload_voices", "preferred_voice": preferred_voice})
		self._voices = response["voices"]
		self._voice = response["current_voice"]
		self._providers = response.get("providers", self._providers)
		self.sample_rate = response.get("sample_rate", self.sample_rate)
		return self._voice

	def get_status(self):
		return {
			"mode": self._mode,
			"providers": list(self._providers),
			"streaming": bool(self._streaming),
			"voiceCount": len(self._voices),
			"currentVoice": self._voice,
			"pid": self._process.pid if self._process is not None else None,
			"helperLog": get_helper_log_path(),
		}

	def synthesize_to_int16(self, text, speed=1.0, voice=None, volume=1.0, language="en-us", generation=None):
		response = self._request(
			{
				"op": "synthesize",
				"text": text,
				"speed": speed,
				"voice": voice,
				"volume": volume,
				"language": language,
				"generation": generation,
			}
		)
		return memoryview(base64.b64decode(response["audio_b64"]))

	def cancel(self, generation):
		"""Tell the helper to drop speech up to this generation. Never waits for synthesis."""
		process = self._process
		if process is None or process.poll() is not None:
			return False
		try:
			self._send_line(process, {"op": "cancel", "generation": generation})
		except Exception:
			return False
		return True

	def stream_synthesize_to_int16(self, text, speed=1.0, voice=None, volume=1.0, language="en-us", generation=None):
		if not self._streaming:
			yield self.synthesize_to_int16(text, speed=speed, voice=voice, volume=volume, language=language, generation=generation)
			return
		payload = {
			"op": "synthesize_stream",
			"text": text,
			"speed": speed,
			"voice": voice,
			"volume": volume,
			"language": language,
			"generation": generation,
		}
		yield from self._stream_request(payload)

	def _stream_request(self, payload):
		with self._io_lock:
			self._ensure_running_locked()
			self._request_id += 1
			payload = dict(payload)
			payload["id"] = self._request_id
			start_time = time.perf_counter()
			with self._state_lock:
				self._request_active = True
				self._request_started_at = start_time
				self._request_interrupted = False
			chunks = 0
			try:
				self._send_line(self._process, payload)
				while True:
					response = self._read_message()
					if response.get("id") != payload["id"]:
						self.logger.warning("Ignored out-of-order XTTS helper response: %s", response.get("type"))
						continue
					if not response.get("ok"):
						self.logger.warning("XTTS helper stream request failed. op=%s error=%s", payload.get("op"), response.get("error"))
						raise RuntimeError(response.get("error", "Unknown helper error"))
					response_type = response.get("type")
					if response_type == "audio_chunk":
						chunks += 1
						yield memoryview(base64.b64decode(response["audio_b64"]))
						continue
					if response_type == "done":
						return
					self.logger.warning("Ignored unknown XTTS helper stream message: %s", response_type)
			except Exception:
				with self._state_lock:
					interrupted = self._request_interrupted
				if interrupted:
					raise HelperRequestInterrupted("Helper request interrupted")
				raise
			finally:
				elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
				self.logger.info(
					"XTTS helper stream response. chars=%s voice=%s chunks=%s elapsedMs=%s",
					len(payload.get("text", "")),
					payload.get("voice") or self._voice,
					chunks,
					elapsed_ms,
				)
				with self._state_lock:
					self._request_active = False
					self._request_started_at = None
					self._request_interrupted = False

	def stream_synthesize_preview_to_int16(self, text, conditioning_path, language="en-us", cache_key=None, synthesis_settings=None):
		yield from self._stream_request({
			"op": "synthesize_preview_stream", "text": text,
			"conditioning_path": conditioning_path, "language": language,
			"cache_key": cache_key, "synthesis_settings": synthesis_settings,
		})

	def synthesize_preview_to_int16(self, text, voice_path, speed=1.0, volume=1.0, language="en-us", cache_key=None):
		response = self._request(
			{
				"op": "synthesize_preview",
				"text": text,
				"voice_path": voice_path,
				"speed": speed,
				"volume": volume,
				"language": language,
				"cache_key": cache_key,
			}
		)
		return memoryview(base64.b64decode(response["audio_b64"]))

	def clone_voice(self, reference_paths, conditioning_path, options):
		self._request({"op": "clone_voice", "reference_paths": reference_paths,
			"conditioning_path": conditioning_path, "options": options})

	def validate_conditioning_file(self, conditioning_path):
		response = self._request(
			{
				"op": "validate_conditioning",
				"conditioning_path": conditioning_path,
			}
		)
		return response.get("result") or {}

	def get_cache_stats(self):
		return self._request({"op": "get_cache_stats"})

	def clear_cache(self):
		return self._request({"op": "clear_cache"})

	def compact_cache(self):
		return self._request({"op": "compact_cache"})

	def segment_text(self, text, language="en-us", max_tokens=None, target_tokens=None, first_chunk_tokens=None):
		text = re.sub(r"\s+", " ", (text or "")).strip()
		if not text:
			return []
		return [text]

	def close(self):
		self._closed = True
		self._stop_process()

	def _stop_process(self):
		with self._io_lock:
			process = self._process
			self._process = None
			if process is None:
				return
			try:
				if process.stdin and process.stdout and process.poll() is None:
					process.stdin.write(json.dumps({"id": 0, "op": "shutdown"}) + "\n")
					process.stdin.flush()
					try:
						process.wait(timeout=0.25)
					except Exception:
						pass
			except Exception:
				pass
			try:
				if process.poll() is None:
					process.terminate()
			except Exception:
				pass
		self.logger.info("MaxLogic XTTS v2 helper closed. pid=%s", process.pid)

	def interrupt(self, reason="stale speech", min_active_ms=0):
		with self._state_lock:
			process = self._process
			request_active = self._request_active
			request_started_at = self._request_started_at
			if process is None or process.poll() is not None or not request_active:
				return False
			elapsed_ms = round((time.perf_counter() - request_started_at) * 1000, 1) if request_started_at else None
			if elapsed_ms is not None and elapsed_ms < max(0, min_active_ms):
				return False
			self._request_interrupted = True
			self._skip_next_prewarm = True
		self.logger.info(
			"Interrupting MaxLogic XTTS v2 helper. pid=%s reason=%s activeElapsedMs=%s",
			process.pid,
			reason,
			elapsed_ms,
		)
		try:
			process.terminate()
		except Exception:
			pass
		try:
			process.wait(timeout=0.15)
		except Exception:
			try:
				process.kill()
			except Exception:
				pass
		return True
