"""Duration-only estimate of reference contributions, without model imports.

Shared by the manager (estimates before cloning) and the helper engine
(actual balanced slicing), so both use the same allocation.
"""
import math

# XTTS skips GPT conditioning chunks shorter than this.
MIN_CHUNK_SECONDS = 0.33


def style_seconds(durations, max_ref_length, gpt_cond_len, gpt_cond_chunk_len):
	"""Return usable GPT prefix overlap in list order; unknown inputs stay unknown.

	This mirrors stock XTTS: the recordings are joined and only the first
	gpt_cond_len seconds are used. Durations must describe the prepared audio
	for an exact estimate.
	"""
	if any(value is None for value in durations):
		return [None] * len(durations)
	lengths = [min(max(0.0, float(value)), max_ref_length) for value in durations]
	end = min(sum(lengths), gpt_cond_len)
	full_chunks = math.floor(end / gpt_cond_chunk_len)
	tail = end - full_chunks * gpt_cond_chunk_len
	if 0 < tail < MIN_CHUNK_SECONDS:
		end -= tail
	position = 0.0
	result = []
	for length in lengths:
		result.append(max(0.0, min(position + length, end) - position))
		position += length
	return result


def balanced_style_seconds(durations, max_ref_length, gpt_cond_len):
	"""Share the GPT style budget fairly across recordings, independent of order.

	Each recording gets an equal share; a recording shorter than its share gives
	its unused time to the others. Unknown durations make the result unknown.
	"""
	if any(value is None for value in durations):
		return [None] * len(durations)
	lengths = [min(max(0.0, float(value)), max_ref_length) for value in durations]
	result = [0.0] * len(lengths)
	remaining = float(min(gpt_cond_len, sum(lengths)))
	pending = sorted(range(len(lengths)), key=lambda index: lengths[index])
	while pending:
		index = pending.pop(0)
		share = min(lengths[index], remaining / (len(pending) + 1))
		result[index] = share
		remaining -= share
	return result


def centered_window(length, amount):
	"""Return (start, end) of a window of amount centred inside length.

	The middle of a recording is usually more representative than its first
	seconds, which often hold a breath, a click or a slow start.
	"""
	amount = min(amount, length)
	start = (length - amount) / 2.0
	return start, start + amount
