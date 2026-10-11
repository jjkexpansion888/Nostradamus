# Five Seconds

A prediction market on what happens next in video, as a swipeable feed.

Each card plays a 5-second clip pulled from anywhere in the archive. A Weights & Biases model watches only that clip, writes one yes/no question about the next 5 seconds, and prices it. You buy Yes or No at that price. The following indexed segment plays in the same card and settles the trade, and your buying power moves.

The honest constraint shaped the product: an archive cannot predict the future, but it can hold out the next segment. Every price is settled by ground truth the forecaster never saw.

## Live app

[https://team-3-app.thecosmoslabs.com/app/](https://team-3-app.thecosmoslabs.com/app/)

Also: [https://workshop.thecosmoslabs.com](https://workshop.thecosmoslabs.com) → App. Hard-refresh if the page looks old.

## Sponsor tools

1. **VAST** — semantic search across the index, consecutive-segment lookup, metadata, and proxied playback
2. **NVIDIA (via index)** — Cosmos Reason captions, Embed1 vectors, and YOLO detections on every segment
3. **Weights & Biases** — Serverless Inference, Llama 3.1 8B: writes and prices the question from the current caption, then settles it from the next
4. **Manifold** — the same question listed as a real play-money binary, opened at the model's price, bet, and resolved

## Coverage

The feed draws from the whole archive, not one camera: Toronto (1080 segments), New York (1005), San Francisco (725), a neighborhood cam (307), Nashville (180), an indoor space (180), and a warehouse (60). `FEED_QUERIES` in [main.py](main.py) spreads the search across those scenes, then pairs are shuffled so the feed cuts between cities.

## How a card works

| Step | What happens |
|------|--------------|
| Clip | `arena_new_round` takes the next consecutive segment pair from a shuffled pool |
| Question | `_market_for_clip` asks W&B for `{question, probability, reason}` grounded in the current caption, constrained to be decidable from a written description of the next 5 seconds |
| Trade | You pick 10, 25, or 50 contracts and tap Yes or No. `arena_lock` debits `contracts × price`, lists the contract on Manifold, and bets your side |
| Settle | `arena_resolve` has W&B judge the next caption against the question, pays M$1 per contract if you were right, and resolves the contract |

Each contract pays M$1, so the price is the cost per contract. Buying 10 No at 70¢ costs M$7.00 and returns M$10.00 if it resolves No.

The contract is listed when you trade, not when a card loads, so scrolling costs no mana. If the model cannot produce a usable question, a generic fallback claim is used and scored by keyword.

The ledger starts at M$100 and lives in the Python process, so a pod restart resets it.

## Front end

React 18 with `htm`, no build step, because the VM has no npm. `react.js`, `react-dom.js`, and `htm.js` are vendored UMD builds served by the same Python process. The feed is CSS scroll-snap, one card per viewport, with two cards prefetched ahead.

## Manifold key

`MANIFOLD_API_KEY` is a pod secret, never a file in git. Without it the app still quotes, trades, and settles against your buying power, and the card says the contract could not be listed.

## API

- `GET /health`
- `POST /api/arena/round` — one clip, one generated question, the model's price
- `POST /api/arena/lock` — `{round_id, outcome: "yes"|"no", contracts: 10}`
- `POST /api/arena/resolve` — `{round_id}`
- `GET /api/stream?source=s3://...` — same-origin video proxy
- `GET /api/cases`, `POST /api/investigate` — an earlier search desk, still served, not in the UI

## Update code on cluster

```bash
NS=team-3
APP_NAME=cyclist-sentinel   # infra names predate the rename
kubectl -n "$NS" create configmap "${APP_NAME}-code" \
  --from-file=tools/cyclist-sentinel/main.py \
  --from-file=tools/cyclist-sentinel/index.html \
  --from-file=tools/cyclist-sentinel/styles.css \
  --from-file=tools/cyclist-sentinel/app.js \
  --from-file=tools/cyclist-sentinel/react.js \
  --from-file=tools/cyclist-sentinel/react-dom.js \
  --from-file=tools/cyclist-sentinel/htm.js \
  --from-file=tools/cyclist-sentinel/README.md \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" rollout restart deploy/"$APP_NAME"
```

Roll back with `kubectl -n team-3 rollout undo deploy/cyclist-sentinel`. The pod calls VSS at `http://video-backend-service.team-3.svc.cluster.local:8000`; the public hostname does not resolve inside the cluster.

## Demo script (about 2 min)

1. Open the feed. A San Francisco street clip plays and the model asks "Will a vehicle pass in front of the Transamerica Pyramid in the next 5 seconds?" at 30¢ Yes.
2. Buy 10 No at 70¢, costing M$7.00.
3. The next 5 seconds play in the same card. It resolves NO, you make +M$3.00, and the balance updates.
4. Swipe up: a New York sidewalk, a new question, a new price.
5. Open the Manifold link to show the same question listed and resolved publicly.
