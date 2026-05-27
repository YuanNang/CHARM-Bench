# CHARM-Bench

CHinese homophonic Associative Reasoning with Multimodality benchmark.

## Overview

CHARM is a multimodal benchmark inspired by a Chinese homophonic puzzle game. Each problem includes two images, a hint word, an answer category, and an answer length. Models can interact multiple times with the environment by submitting guesses. After each incorrect guess, the environment returns feedback on character positions and pinyin positions, which helps the model refine its next attempt.

We thank the game creators for the inspiration and the data source.

- Game store link: https://store.steampowered.com/app/4164310/_/
- Studio website: https://www.findthelamp.com/

## Benchmark Files

We provide benchmark manifests and image bundles for easy download:

- data/charm-bench-100.jsonl with images in data/charm-bench-100.zip (IDs 0-99)

Each manifest row contains:

- id: the benchmark ID (0..N-1)
- source_id: the original game ID
- answer, ref_word, category, answer_length, pinyin_syllables
- image_1, image_2: repo-relative image paths

## Unpack Images

Unzip the images into the data directory so the paths in the manifest resolve correctly:

```bash
# 100-image pack
unzip data/charm-bench-100.zip -d data/benchmark
```

## Run Evaluation

Run an evaluation:

```bash
uv run charm eval \
  --manifest data/charm-bench-100.jsonl \
  --provider openai \
  --model gpt-4.1 \
  --max-attempts 3
``````

Notes:

- If you omit --out, the default path is runs/<normalized-model>/run.jsonl.
- If you omit --manifest, the default path is data/charm-bench-100.jsonl.
- You can resume from checkpoints automatically by re-running the same command.
- --max-attempts controls the max guesses per problem (use unlimited/inf/none for no limit).
- --concurrency controls how many problems run in parallel.
- --limit truncates the manifest to the first N problems.

Provider credentials are read from .env:

```bash
CHARM_API_KEY=your_api_key_here
CHARM_BASE_URL=your_base_url_here
CHARM_PROVIDER=openai
CHARM_MANIFEST=data/charm-bench-100.jsonl
CHARM_MAX_ATTEMPTS=5
CHARM_CONCURRENCY=2
CHARM_TIMEOUT=600
CHARM_MODEL=your_model_name_here
CHARM_MAX_TOKENS=32768
```

## Environment Feedback

After an incorrect guess, the environment returns two feedback streams:

- Character feedback: green = correct position, yellow = wrong position, gray = not present.
- Pinyin feedback: the same rules applied to tone-less pinyin syllables.

If the submitted answer length does not match the expected length, the environment returns a length_mismatch error and skips character and pinyin feedback for that guess.
