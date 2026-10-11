# Nostradamus

**Prediction markets on the next five seconds of video.**

Nostradamus turns an indexed video archive into a swipeable prediction-market feed. Watch a five-second clip, read an AI-generated question about what happens next, and buy Yes or No contracts at the model's quoted price. The next segment is revealed, the question is settled, and your play-money balance updates.

Built at the VAST Builders Challenge hackathon by [jjkexpansion888](https://github.com/jjkexpansion888) in collaboration with [Wilson Wong](https://github.com/wilsonwongxyz). The original [project submission](https://tokensand.com/p/nostradamus) includes the demo and links to the hackathon source repository.

## Demo and Submission

[![Watch the Nostradamus demo](https://cdn.loom.com/sessions/thumbnails/64685c106c01403faeeadb6768536752-f1c292064b1bd943.gif)](https://www.loom.com/share/64685c106c01403faeeadb6768536752)

- [Watch the demo video](https://www.loom.com/share/64685c106c01403faeeadb6768536752)
- [Read the project submission on tokens&](https://tokensand.com/p/nostradamus)
- [Open the hackathon demo app](https://team-3-app.thecosmoslabs.com/app/) (depends on the workshop infrastructure remaining available)

## How It Works

1. **Watch:** the feed selects a pair of consecutive indexed segments and plays the first five seconds. Scenes include pedestrians, vehicles, and intersections.
2. **Forecast:** a model served through Weights & Biases reads only the current segment's caption, generates a yes/no question about the next segment, and quotes a probability with a short explanation.
3. **Trade:** choose a contract quantity and buy Yes or No. Each winning contract pays M$1; the quoted price determines the upfront cost.
4. **Reveal and settle:** the next video segment plays. The model evaluates its caption against the question, pays winning contracts, and updates the balance.
5. **Swipe:** move to another clip and a new market. With a Manifold API key configured, the same question is also created, bet on, and resolved as a Manifold play-money market.

For example, buying 10 No contracts at 70 cents costs M$7. If the next segment resolves No, the payout is M$10 and the profit is M$3.

The next segment is held out from the forecaster. This is a forecasting game over recorded footage: settlement uses AI-generated captions of the revealed segment, rather than a human review of the video.

## Architecture

| Component | Role |
| --- | --- |
| VAST Video Search & Summary (VSS) | Searches the archive, retrieves consecutive segments and metadata, and supplies video playback. |
| NVIDIA Cosmos Reason, Embed1, and YOLO | Produce the captions, embeddings, and detections used by the prebuilt video index. |
| Weights & Biases Serverless Inference | Generates questions and prices, then judges the next segment. The default model is Llama 3.1 8B Instruct. |
| Manifold | Optionally mirrors each trade as a real play-money market. |
| Python standard library | Serves the app, proxies video, manages rounds, and maintains the ledger. |
| React 18 and htm | Render the full-screen, scroll-snap feed using vendored browser builds, with no frontend build step. |

The application code is in [`tools/cyclist-sentinel/`](tools/cyclist-sentinel/). The directory and deployment names predate the Nostradamus name.

## Run Locally

Use Python 3.10 or newer. No pip or npm installation is required, but playable rounds require access to a compatible VSS backend with indexed consecutive video segments. The hackathon's hosted infrastructure and credentials are not included in this repository.

Set the backend and inference credentials in your terminal:

```bash
export VSS_URL="https://your-vss-backend"
export VSS_USERNAME="your-username"
export VSS_PASSWORD="your-password"
export WANDB_API_KEY="your-wandb-api-key"

# Optional: W&B project routing and Manifold integration
export WANDB_TEAM="your-team"
export WANDB_PROJECT="your-project"
export MANIFOLD_API_KEY="your-manifold-api-key"

python3 tools/cyclist-sentinel/main.py
```

Open [http://localhost:8080](http://localhost:8080). Set `PORT` to change the port, or `WANDB_MODEL` to change the inference model. Keep credentials out of source control.

## Prototype Behavior

- The ledger starts at M$100 by default (`STARTING_CASH`) and is shared in the Python process. Restarting the server resets balances and rounds.
- If inference fails or returns an unusable response, the app falls back to a generic question and keyword-based settlement.
- Manifold is optional. Without its API key, the local quoting, trading, and settlement flow still works.
- Prices and verdicts are model outputs; caption omissions can affect settlement.

## Repository Guide

- [`tools/cyclist-sentinel/main.py`](tools/cyclist-sentinel/main.py): backend, forecasting, settlement, video proxy, and Manifold integration.
- [`tools/cyclist-sentinel/app.js`](tools/cyclist-sentinel/app.js): video feed and trading interface.
- [`tools/cyclist-sentinel/README.md`](tools/cyclist-sentinel/README.md): API endpoints, deployment commands, and demo walkthrough from the build.
- [`HACKATHON_GUIDE.md`](HACKATHON_GUIDE.md): original VAST Builders Challenge event and infrastructure instructions.
- [`ARCHITECTURE_REFERENCE.md`](ARCHITECTURE_REFERENCE.md): workshop pipeline and video-index reference.
- [`.cursor/skills/`](.cursor/skills/): workshop skills for ingestion, retrieval, and deployment.

## Credits

This repository preserves the joint hackathon work from [wilsonwongxyz/vast-builders-challenge](https://github.com/wilsonwongxyz/vast-builders-challenge), imported through [jjkexpansion888/vast-builders-challenge](https://github.com/jjkexpansion888/vast-builders-challenge). The original submission remains at [tokensand.com/p/nostradamus](https://tokensand.com/p/nostradamus). The workshop materials and prebuilt infrastructure came from the VAST Builders Challenge; the submission lists VAST Data, NVIDIA, CoreWeave / Weights & Biases, and Cursor among the tools used.
