PYTHON ?= .venv/bin/python

.PHONY: test-data test-live
# Cached Whisper is required; these commands never download weights implicitly.
test-data:
	SRA_RUN_AUDIO_FLOW=1 SRA_RUN_WHISPER_TESTS=1 SRA_RUN_LIVE_E2E=0 HF_HUB_OFFLINE=1 $(PYTHON) -m pytest -m 'not live_openai' -q

test-live:
	SRA_RUN_AUDIO_FLOW=1 SRA_RUN_LIVE_E2E=1 HF_HUB_OFFLINE=1 $(PYTHON) -m pytest tests/integration/test_audio_flow.py -m live_openai -v
