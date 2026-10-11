const { useCallback, useEffect, useRef, useState } = React;
const html = htm.bind(React.createElement);

const SIZES = [10, 25, 50];

const money = (n) => "M$" + Number(n || 0).toFixed(2);
const clamp = (n, max) => (Number.isFinite(n) ? Math.min(max, Math.max(1, Math.trunc(n))) : 1);
const signed = (n) => (Number(n) >= 0 ? "+" : "-") + money(Math.abs(Number(n) || 0));
const streamUrl = (source) => "api/stream?source=" + encodeURIComponent(source);

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

function Card({ round, active, size, onSize, cash, onTraded }) {
  const videoRef = useRef(null);
  const [phase, setPhase] = useState("open");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [blocked, setBlocked] = useState(false);

  const clip = (result && result.next) || round.context;

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (!active) {
      video.pause();
      return;
    }
    video.muted = true;
    const go = video.play();
    if (go && go.catch) go.catch(() => setBlocked(true));
  }, [active, clip && clip.source]);

  const trade = useCallback(
    async (outcome) => {
      if (phase !== "open") return;
      setPhase("pending");
      setError("");
      try {
        const order = await api("api/arena/lock", {
          round_id: round.round_id,
          outcome,
          contracts: size,
        });
        onTraded(order.balance);
        const settled = await api("api/arena/resolve", { round_id: round.round_id });
        setResult({ ...settled, order: order.order, market: order.manifold });
        onTraded(settled.balance);
        setPhase("done");
      } catch (err) {
        setError(err.message);
        setPhase("open");
      }
    },
    [phase, round, size, onTraded]
  );

  const q = round.quote;
  const scene = [clip && clip.location, clip && clip.camera_id].filter(Boolean).join(" · ");
  const purse = cash == null ? 0 : cash;
  const yesCost = (q.yes_cents / 100) * size;
  const noCost = (q.no_cents / 100) * size;
  // The cheaper side sets the ceiling, so one expensive side never caps the other.
  const cheapest = Math.min(q.yes_cents, q.no_cents) / 100;
  const ceiling = Math.max(1, Math.min(round.max_contracts || 250, Math.floor(purse / cheapest)));

  return html`
    <section class="card">
      <video
        ref=${videoRef}
        class="bg"
        src=${clip && clip.source ? streamUrl(clip.source) : undefined}
        playsinline
        muted
        loop
        preload="auto"
      ></video>
      ${blocked && html`
        <button type="button" class="tap" onClick=${() => {
          const v = videoRef.current;
          if (v) { v.muted = true; v.play().then(() => setBlocked(false), () => {}); }
        }}>Tap to play</button>
      `}

      <div class="scrim"></div>

      <div class="meta">
        <span class="tag">${result ? "Next 5 seconds" : "Now"}</span>
        <span class="scene">${scene}</span>
      </div>

      <div class="panel">
        ${phase !== "done" && html`
          <p class="question">${round.question}</p>
          <p class="why">${q.reason}</p>
          <div class="sizer">
            <div class="stepper">
              <button
                type="button"
                disabled=${phase !== "open" || size <= 1}
                onClick=${() => onSize(clamp(size - 1, ceiling))}
              >−</button>
              <input
                type="number"
                inputmode="numeric"
                value=${size}
                min="1"
                max=${ceiling}
                disabled=${phase !== "open"}
                onChange=${(e) => onSize(clamp(parseInt(e.target.value, 10), ceiling))}
                onBlur=${(e) => (e.target.value = String(size))}
              />
              <button
                type="button"
                disabled=${phase !== "open" || size >= ceiling}
                onClick=${() => onSize(clamp(size + 1, ceiling))}
              >+</button>
            </div>
            <div class="chips">
              ${SIZES.filter((n) => n <= ceiling).map((n) => html`
                <button
                  type="button"
                  key=${n}
                  class=${n === size ? "on" : ""}
                  disabled=${phase !== "open"}
                  onClick=${() => onSize(n)}
                >${n}</button>
              `)}
              <button
                type="button"
                class=${size === ceiling ? "on" : ""}
                disabled=${phase !== "open"}
                onClick=${() => onSize(ceiling)}
              >Max</button>
            </div>
          </div>
          <div class="bet">
            <button
              type="button"
              class="yes"
              disabled=${phase !== "open" || yesCost > purse}
              onClick=${() => trade("yes")}
            >
              <b>Yes</b>
              <span>${yesCost > purse ? "needs " + money(yesCost) : q.yes_cents + "¢ · " + money(yesCost)}</span>
            </button>
            <button
              type="button"
              class="no"
              disabled=${phase !== "open" || noCost > purse}
              onClick=${() => trade("no")}
            >
              <b>No</b>
              <span>${noCost > purse ? "needs " + money(noCost) : q.no_cents + "¢ · " + money(noCost)}</span>
            </button>
          </div>
          ${phase === "pending" && html`<p class="note">Settling against the next 5 seconds…</p>`}
          ${error && html`<p class="note err">${error}</p>`}
        `}

        ${phase === "done" && result && html`
          <p class="question small">${round.question}</p>
          <div class=${"outcome " + (result.won ? "won" : "lost")}>
            <span class="verdict">${result.truth ? "YES" : "NO"}</span>
            <span class="pnl">${signed(result.profit)}</span>
          </div>
          <p class="why">
            ${"You bought " + result.order.contracts + " " + result.order.outcome.toUpperCase() +
              " at " + result.order.price_cents + "¢. " + result.reason}
          </p>
          <p class="note">
            Swipe up for the next clip${result.market && result.market.url ? " · " : ""}
            ${result.market && result.market.url && html`
              <a href=${result.market.url} target="_blank" rel="noopener">see it on Manifold</a>
            `}
          </p>
        `}
      </div>
    </section>
  `;
}

function App() {
  const [rounds, setRounds] = useState([]);
  const [active, setActive] = useState(0);
  const [balance, setBalance] = useState(null);
  const [size, setSize] = useState(10);
  const [fatal, setFatal] = useState("");
  const loading = useRef(false);
  const scroller = useRef(null);

  const addRound = useCallback(async () => {
    if (loading.current) return;
    loading.current = true;
    try {
      const data = await api("api/arena/round");
      setRounds((prev) => [...prev, data]);
      setBalance((prev) => prev || data.balance);
    } catch (err) {
      setFatal(err.message);
    } finally {
      loading.current = false;
    }
  }, []);

  useEffect(() => {
    addRound().then(addRound);
  }, [addRound]);

  useEffect(() => {
    if (rounds.length && rounds.length - active < 3) addRound();
  }, [active, rounds.length, addRound]);

  useEffect(() => {
    const root = scroller.current;
    if (!root) return;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const idx = Number(entry.target.dataset.index);
            if (!Number.isNaN(idx)) setActive(idx);
          }
        });
      },
      { root, threshold: 0.6 }
    );
    Array.from(root.children).forEach((child) => observer.observe(child));
    return () => observer.disconnect();
  }, [rounds.length]);

  const cash = balance ? balance.cash : null;
  const record = balance ? balance.won + "/" + balance.settled : "0/0";

  return html`
    <div class="app">
      <header class="hud">
        <div class="brand">
          <strong>Five Seconds</strong>
          <span>what happens next, priced</span>
        </div>
        <div class="wallet">
          <span class="cash">${cash == null ? "—" : money(cash)}</span>
          <span class="rec">${record} settled</span>
        </div>
      </header>

      <div class="feed" ref=${scroller}>
        ${rounds.map((round, i) => html`
          <div class="slot" key=${round.round_id} data-index=${i}>
            <${Card}
              round=${round}
              active=${i === active}
              size=${size}
              onSize=${setSize}
              cash=${cash}
              onTraded=${setBalance}
            />
          </div>
        `)}
        ${!rounds.length && html`
          <div class="slot">
            <section class="card empty">
              <p>${fatal || "Loading the feed…"}</p>
            </section>
          </div>
        `}
      </div>
    </div>
  `;
}

ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
