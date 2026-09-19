"""XTTS GPT decoding that replays each step as one CUDA graph. Helper process only: imports torch.

Coqui decodes through Hugging Face generate(), which launches about 700 small GPU
kernels per audio token from Python. On Windows that overhead, not the GPU,
limits speed: about 36 ms per token, barely faster than real time. Recording one
decoding step as a CUDA graph and replaying it takes about 12 ms per token.

A graph needs fixed tensor shapes, so the key/value cache here has a fixed size
and attention masks the unused part. Sampling mirrors the Hugging Face logits
processors Coqui uses, in the same order: repetition penalty, temperature,
top-k, top-p. With the same seed it chooses the same tokens as Coqui.
"""
import torch
import torch.nn.functional as F


class StaticGPTGenerator(object):
	def __init__(self, gpt):
		self.gpt = gpt
		self.inference = gpt.gpt_inference
		self.transformer = self.inference.transformer
		config = self.transformer.config
		self.heads = config.n_head
		self.width = config.n_embd
		self.head_width = self.width // self.heads
		self.capacity = config.n_positions
		parameter = next(self.transformer.parameters())
		self.device = parameter.device
		shape = (len(self.transformer.h), 1, self.heads, self.capacity, self.head_width)
		self.keys = torch.zeros(shape, device=self.device, dtype=parameter.dtype)
		self.values = torch.zeros_like(self.keys)
		self.positions = torch.arange(self.capacity, device=self.device)
		# Inputs and outputs of the recorded step. Replaying the graph reads and writes these tensors.
		self.token = torch.zeros(1, 1, dtype=torch.long, device=self.device)
		self.cache_position = torch.zeros(1, dtype=torch.long, device=self.device)
		self.audio_position = torch.zeros(1, dtype=torch.long, device=self.device)
		self.graph = None
		self.graph_logits = None
		self.graph_latent = None

	def _layers(self, hidden, cache_positions):
		"""Run the transformer on hidden, store its keys and values at cache_positions. Returns logits and latent."""
		length = hidden.shape[1]
		mask = (self.positions[None, :] <= cache_positions[:, None]).view(1, 1, length, self.capacity)
		for number, block in enumerate(self.transformer.h):
			query, key, value = block.attn.c_attn(block.ln_1(hidden)).split(self.width, dim=2)
			query = query.view(1, length, self.heads, self.head_width).transpose(1, 2)
			key = key.view(1, length, self.heads, self.head_width).transpose(1, 2)
			value = value.view(1, length, self.heads, self.head_width).transpose(1, 2)
			self.keys[number].index_copy_(2, cache_positions, key)
			self.values[number].index_copy_(2, cache_positions, value)
			attended = F.scaled_dot_product_attention(query, self.keys[number], self.values[number], attn_mask=mask)
			hidden = hidden + block.attn.c_proj(attended.transpose(1, 2).reshape(1, length, self.width))
			hidden = hidden + block.mlp(block.ln_2(hidden))
		# As Coqui's sample_stream: the latent is final_norm of the last hidden state, the logits come from it.
		latent = self.inference.final_norm(self.transformer.ln_f(hidden)[:, -1])
		return self.inference.lm_head[1](latent), latent

	def _step(self):
		embedding = self.inference.embeddings(self.token) + self.inference.pos_embedding.emb(self.audio_position)[None]
		return self._layers(embedding, self.cache_position)

	def _record(self):
		# Warm-up and recording write keys and values at cache_position. The last slot is never read
		# by a real step, so the prompt already in the cache stays intact.
		self.cache_position.fill_(self.capacity - 1)
		stream = torch.cuda.Stream()
		stream.wait_stream(torch.cuda.current_stream())
		with torch.cuda.stream(stream):
			for __ in range(3):
				self._step()
		torch.cuda.current_stream().wait_stream(stream)
		graph = torch.cuda.CUDAGraph()
		with torch.cuda.graph(graph):
			self.graph_logits, self.graph_latent = self._step()
		self.graph = graph

	@torch.inference_mode()
	def get_generator(self, fake_inputs, top_k=50, top_p=0.85, temperature=0.75, repetition_penalty=10.0,
			do_sample=True, **unused):
		"""Yield (token, latent) pairs like Coqui's GPT.get_generator."""
		prefix = self.inference.cached_prefix_emb
		prefix_length = prefix.shape[1]
		start = torch.full((1, 1), self.gpt.start_audio_token, dtype=torch.long, device=self.device)
		first_position = torch.zeros(1, dtype=torch.long, device=self.device)
		start_embedding = self.inference.embeddings(start) + self.inference.pos_embedding.emb(first_position)[None]
		hidden = torch.cat([prefix.to(start_embedding.dtype), start_embedding], dim=1)
		logits, latent = self._layers(hidden, torch.arange(prefix_length + 1, device=self.device))
		vocabulary = logits.shape[-1]
		# The repetition penalty covers every token in the input, the prompt placeholders included.
		seen = torch.zeros(vocabulary, dtype=torch.bool, device=self.device)
		seen[fake_inputs[0]] = True
		if self.graph is None:
			self._record()
		for number in range(self.gpt.max_gen_mel_tokens):
			scores = logits.float()
			if repetition_penalty != 1.0:
				penalized = torch.where(scores < 0, scores * repetition_penalty, scores / repetition_penalty)
				scores = torch.where(seen[None], penalized, scores)
			scores = scores / temperature
			if top_k:
				kth = torch.topk(scores, min(top_k, vocabulary))[0][..., -1, None]
				scores = scores.masked_fill(scores < kth, float("-inf"))
			if top_p < 1.0:
				sorted_scores, order = torch.sort(scores, descending=False)
				remove = sorted_scores.softmax(dim=-1).cumsum(dim=-1) <= (1 - top_p)
				remove[..., -1:] = False
				scores = scores.masked_fill(remove.scatter(1, order, remove), float("-inf"))
			if do_sample:
				token = torch.multinomial(scores.softmax(dim=-1), num_samples=1)[:, 0]
			else:
				token = scores.argmax(dim=-1)
			yield token, latent.float()
			if int(token) == self.gpt.stop_audio_token or prefix_length + 2 + number >= self.capacity:
				return
			seen[token] = True
			self.token.copy_(token[:, None])
			self.cache_position.fill_(prefix_length + 1 + number)
			self.audio_position.fill_(number + 1)
			self.graph.replay()
			logits, latent = self.graph_logits.clone(), self.graph_latent.clone()
