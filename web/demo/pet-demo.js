/* The desk pet in a web page (#116): the box's face screen, drawn from a pet pack (pets/<name>/pet.json and its
 * frames.png) the way the firmware draws it, for people to try before they flash a box. Sample numbers stand in for
 * GitHub. No framework and no build step: import mountPetDemo, give it an element and the pack's pet.json URL.
 *
 * What follows the firmware (projects/pet/main): the 720x720 layout and fonts of face.c, the 48x40 art drawn 12x at
 * (72, 120), the sprite player of anim.c (idle animations drawn by weight per mood with a 0.8 to 3 s rest between,
 * reactions that preempt them, a one-shot reaction played twice, the sleep loop), the hearts, the falling snack, the
 * speech bubble tinted by the waiting tool, the stats overlay, and the banners. The gestures are the box's (main.c):
 * stroke the pet for hearts, swipe up or down from off the pet for the stats, tap the bubble for the next session, tap
 * to acknowledge. Every action is also a button under the screen. */

const W = 48, H = 40, COLS = 8;                   /* pet_def.h, pets/README.md: frame k is row k / 8, column k % 8 */
const SCALE = 12, X = 72, Y = 120;                /* pet_config.h: PET_SPRITE_SCALE, PET_SPRITE_X, PET_SPRITE_Y */
const HEART_SCALE = 6, SNACK_SCALE = 4;
const STROKE_PX = 150, TAP_PX = 20, JITTER_PX = 3;   /* pet_config.h PET_STROKE_* */
const SWIPE_PX = 50;                              /* LVGL's default gesture limit */
const BANNER_MS = 4000, STATS_MS = 12000, MAX_HEARTS = 4;
const HAPPY_FEED = 8, HAPPY_MERGE = 5, HAPPY_PET = 3, PET_FADE = 4;
const MOODS = ["sad", "neutral", "happy", "ecstatic", "worried"];
const MOOD_SCORE = { sad: 2, neutral: 5, happy: 7, ecstatic: 10, worried: 4 };
const SOURCE_COLOR = { claude: "#D97757", codex: "#10A37F", local: "#B794F4" };   /* pet_config.h PET_COLOR_* */
const OTHER_COLOR = "#9AA5B1";
const KIND_TEXT = { needs_input: "needs your input", done: "finished", error: "hit an error" };
const mounts = new WeakMap();   /* element -> { token, destroy }: one live demo per element */
const SESSIONS = [   /* what "A session waits" brings, in turn */
  { session: "desk-pet", source: "claude", kind: "needs_input", title: "Flash the new build to the box now?" },
  { session: "website", source: "codex", kind: "done", title: "Added the live pet to the desk pet page" },
  { session: "pi-tools", source: "local", kind: "error", title: "2 of 48 tests failed" },
];

/* Read a pet pack: pet.json and the sheet it names, cut into 48x40 ink masks (petpack.py is_ink: opaque and dark). */
export async function loadPack(url) {
  const base = new URL(url, document.baseURI);
  const res = await fetch(base);
  if (!res.ok) throw new Error(`pet pack: ${res.status} for ${base}`);
  const def = await res.json();
  const img = new Image();
  img.crossOrigin = "anonymous";   /* a pack on another origin needs CORS, or the canvas can't be read */
  img.src = new URL(def.sheet || "frames.png", base).href;
  await img.decode().catch(() => {
    throw new Error(`pet pack: could not load ${img.src}`);
  });
  const c = document.createElement("canvas");
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const g = c.getContext("2d", { willReadFrequently: true });
  g.drawImage(img, 0, 0);
  const px = g.getImageData(0, 0, c.width, c.height).data;
  const frames = [];
  for (let k = 0; k < COLS * Math.floor(c.height / H); k++) {
    const fx = (k % COLS) * W, fy = Math.floor(k / COLS) * H, mask = new Uint8Array(W * H);
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = ((fy + y) * c.width + fx + x) * 4;
        mask[y * W + x] = px[i + 3] >= 128 && px[i] + px[i + 1] + px[i + 2] < 3 * 128 ? 1 : 0;
      }
    }
    frames.push(mask);
  }
  const steps = (list) => (list || []).map((f) => ({ img: f.frame, dx: f.dx || 0, dy: f.dy || 0, ms: f.ms || 500 }));
  const anims = Object.fromEntries(Object.entries(def.animations).map(([name, list]) => [name, steps(list)]));
  return {
    name: def.name,
    colors: def.colors,
    frames,
    anims,
    moods: def.moods,
    snack: steps(def.play.snack),
    heart: steps(def.play.heart),
    petAnim: anims["happy/wiggle"] ? "happy/wiggle" : "celebrate",   /* anim.c: what petting plays */
  };
}

function paint(canvas, mask, color) {
  const g = canvas.getContext("2d");
  const im = g.createImageData(W, H);
  const n = parseInt(color.slice(1), 16), r = n >> 16, gr = (n >> 8) & 255, b = n & 255;
  for (let i = 0; i < W * H; i++) {
    if (mask[i]) {
      im.data.set([r, gr, b, 255], i * 4);
    }
  }
  g.putImageData(im, 0, 0);
}

function el(tag, cls, parent, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}

function art(parent, cls, scale) {
  const c = el("canvas", cls, parent);
  c.width = W;
  c.height = H;
  c.style.width = `${W * scale}px`;
  c.style.height = `${H * scale}px`;
  return c;
}

/* anim.c, the sprite player, on timeouts instead of an LVGL timer. */
class Player {
  constructor(pack, draw, still) {
    this.pack = pack;
    this.draw = draw;            /* (frame, dx, dy) */
    this.still = still;          /* reduced motion: rest between reactions, no idle animations */
    this.mood = "happy";
    this.sleeping = false;
    this.anim = null;            /* name, or null when resting */
    this.frame = 0;
    this.reaction = false;
    this.loop = false;
    this.repeat = 0;
    this.timer = 0;
  }

  after(ms) {
    clearTimeout(this.timer);
    if (!this.stopped) this.timer = setTimeout(() => this.tick(), ms);
  }

  rest() {
    this.anim = null;
    this.draw(this.pack.moods[this.mood].rest, 0, 0);
    if (!this.still) this.after(800 + Math.random() * 2200);   /* the resting face for 0.8 .. 3.0 s */
  }

  pickIdle() {
    const set = Object.entries(this.pack.moods[this.mood].idle);
    let r = Math.random() * set.reduce((t, [, w]) => t + w, 0);
    for (const [name, w] of set) {
      if (r < w) return name;
      r -= w;
    }
    return set[0][0];
  }

  start(name, reaction) {
    this.anim = name;
    this.frame = 0;
    this.reaction = reaction;
    const f = this.pack.anims[name][0];
    this.draw(f.img, f.dx, f.dy);
    if (!(this.still && name === "sleep")) this.after(f.ms);   /* reduced motion: asleep is one still frame */
  }

  tick() {
    if (this.anim === null) {
      if (this.sleeping) this.start("sleep", true);
      else this.start(this.pickIdle(), false);
      return;
    }
    const a = this.pack.anims[this.anim];
    if (++this.frame >= a.length) {
      if (this.reaction && this.loop) {
        this.frame = 0;
      } else if (this.reaction && this.repeat > 0) {
        this.repeat--;
        this.frame = 0;
      } else if (this.sleeping) {
        this.loop = true;
        this.start("sleep", true);
        return;
      } else {
        this.rest();
        return;
      }
    }
    const f = a[this.frame];
    this.draw(f.img, f.dx, f.dy);
    this.after(f.ms);
  }

  setMood(mood) {
    this.mood = mood;
    if (this.anim === null || !this.reaction) this.rest();
  }

  play(name) {   /* anim_play(reaction, false): once, then once more */
    this.loop = false;
    this.repeat = 1;
    this.start(name, true);
  }

  pet() {
    if (this.anim === this.pack.petAnim) return;   /* already wiggling: keep going */
    this.loop = false;
    this.repeat = 0;
    this.start(this.pack.petAnim, true);
  }

  setSleeping(sleeping) {
    if (this.sleeping === sleeping) return;
    this.sleeping = sleeping;
    if (sleeping) {
      this.loop = true;
      this.start("sleep", true);
    } else if (this.anim === "sleep") {
      this.loop = false;
      this.rest();
    }
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
  }
}

/* Mount the demo in root, replacing any demo already there. options.pack: the pet.json URL. Returns { destroy }. When
 * a later call on the same element starts before this one's pack loads, this one gives up and returns a no-op. */
export async function mountPetDemo(root, options = {}) {
  mounts.get(root)?.destroy?.();
  const token = {};
  mounts.set(root, { token });
  const pack = await loadPack(options.pack || "pet.json").catch((e) => {
    if (mounts.get(root)?.token === token) mounts.delete(root);
    throw e;
  });
  if (mounts.get(root)?.token !== token) return { destroy() {} };
  const c = pack.colors;
  let destroyed = false;
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const timers = new Set();
  const later = (fn, ms) => {
    const t = setTimeout(() => {
      timers.delete(t);
      fn();
    }, ms);
    timers.add(t);
    return t;
  };
  const cancel = (t) => {
    clearTimeout(t);
    timers.delete(t);
  };

  const state = {
    level: 4, xpInto: 201, xpNext: 800, xp: 2601, streak: 19, today: 12, goal: 20,
    commits: 7, prs: 2, issues: 2, reviews: 1, other: 0, todayXp: 64,
    mood: "happy", happiness: 72, snacks: 3, eaten: 0, sleeping: false, merged: 116,
    queue: [], pos: 0, next: 0, strokes: 0, hearts: 0,
  };

  root.classList.add("pet-demo");
  root.replaceChildren();
  for (const [k, v] of Object.entries(c)) root.style.setProperty(`--pd-${k.replace("_", "-")}`, v);

  const screen = el("div", "pet-demo__screen", root);
  screen.tabIndex = -1;
  const stage = el("div", "pet-demo__stage", screen);
  stage.setAttribute("role", "img");
  const live = el("p", "pet-demo__live", root);
  live.setAttribute("aria-live", "polite");

  const sprite = art(stage, "pet-demo__sprite", SCALE);
  const snack = art(stage, "pet-demo__snack", SNACK_SCALE);
  snack.hidden = true;
  const hearts = el("div", "pet-demo__hearts", stage);

  const level = el("div", "pet-demo__level", stage);
  const bar = el("div", "pet-demo__bar", stage);
  const barFill = el("div", "pet-demo__bar-fill", bar);
  const xpText = el("div", "pet-demo__xp", stage);
  const clock = el("div", "pet-demo__clock", stage);
  const streak = el("div", "pet-demo__streak", stage);
  const today = el("div", "pet-demo__today", stage);
  const mood = el("div", "pet-demo__mood", stage);
  el("div", "pet-demo__link", stage, "wifi + mqtt ok");
  const status = el("div", "pet-demo__status", stage);
  const banner = el("div", "pet-demo__banner", stage);
  banner.hidden = true;

  const bubble = el("div", "pet-demo__bubble", stage);
  bubble.hidden = true;
  const who = el("div", "pet-demo__who", bubble);
  const what = el("div", "pet-demo__what", bubble);
  const title = el("div", "pet-demo__title", bubble);
  const count = el("div", "pet-demo__count", bubble);

  const stats = el("div", "pet-demo__stats", stage);
  stats.hidden = true;
  el("div", "pet-demo__stats-head", stats, "stats");
  const statsBody = el("div", "pet-demo__stats-body", stats);
  el("div", "pet-demo__stats-head", stats, "play");
  const playBody = el("div", "pet-demo__stats-body", stats);
  const statsBar = el("div", "pet-demo__bar pet-demo__bar--stats", stats);
  const statsFill = el("div", "pet-demo__bar-fill", statsBar);
  el("div", "pet-demo__stats-foot", stats, "tap to close");

  /* The buttons: every gesture, for keyboards and for people who don't know them. */
  const controls = el("div", "pet-demo__controls", root);
  const button = (label, fn) => {
    const b = el("button", "pet-demo__button", controls, label);
    b.type = "button";
    b.addEventListener("click", fn);
    return b;
  };
  button("Pet", () => petAt(X + (W * SCALE) / 2, Y + (H * SCALE) / 3));
  button("Feed", () => feed());
  button("A session waits", () => sessionWaits());
  button("Merge a PR", () => merged());
  button("Stats", () => toggleStats());
  const night = button("Night", () => setSleeping(!state.sleeping));
  const moodPick = el("select", "pet-demo__select", controls);
  moodPick.setAttribute("aria-label", "Mood");
  for (const m of MOODS) el("option", null, moodPick, m).value = m;
  moodPick.value = state.mood;
  moodPick.addEventListener("change", () => setMood(moodPick.value));

  /* Where the pet's ink is, from its resting frames (face.c find_pet_box), so a tap there is a tap on the pet. */
  const box = { x1: W, y1: H, x2: -1, y2: -1 };
  for (const m of MOODS) {
    const mask = pack.frames[pack.moods[m].rest];
    for (let i = 0; i < W * H; i++) {
      if (!mask[i]) continue;
      const x = i % W, y = Math.floor(i / W);
      box.x1 = Math.min(box.x1, x);
      box.x2 = Math.max(box.x2, x);
      box.y1 = Math.min(box.y1, y);
      box.y2 = Math.max(box.y2, y);
    }
  }
  if (box.x2 < 0) Object.assign(box, { x1: 0, y1: 0, x2: W - 1, y2: H - 1 });   /* blank resting frames: the sprite */
  const onPet = (p) => !bubbleHit(p) && stats.hidden && p.x >= X + box.x1 * SCALE && p.x < X + (box.x2 + 1) * SCALE &&
    p.y >= Y + box.y1 * SCALE && p.y < Y + (box.y2 + 1) * SCALE;
  const bubbleHit = (p) => {
    if (bubble.hidden || !stats.hidden) return false;
    const s = stage.getBoundingClientRect(), r = bubble.getBoundingClientRect(), k = s.width / 720;
    return p.x >= (r.left - s.left) / k && p.x <= (r.right - s.left) / k &&
      p.y >= (r.top - s.top) / k && p.y <= (r.bottom - s.top) / k;
  };

  let shown = { frame: pack.moods.happy.rest, dx: 0, dy: 0 };
  const ink = () => {
    const n = state.queue.length ? state.queue[state.pos] : null;
    return n ? SOURCE_COLOR[n.source] || OTHER_COLOR : state.sleeping ? c.sleep_ink : c.ink;
  };
  const draw = (frame, dx, dy) => {
    shown = { frame, dx, dy };
    paint(sprite, pack.frames[frame], ink());
    sprite.style.transform = `translate(${X + dx * SCALE}px, ${Y + dy * SCALE}px)`;
  };
  const player = new Player(pack, draw, still);

  let bannerTimer = 0, statsTimer = 0;
  const say = (text) => {
    live.textContent = text;
  };
  const showBanner = (text, color) => {
    banner.textContent = text;
    banner.style.color = color;
    banner.hidden = false;
    cancel(bannerTimer);
    bannerTimer = later(() => (banner.hidden = true), BANNER_MS);
    say(text);
  };
  const hideStats = () => {
    cancel(statsTimer);
    stats.hidden = true;
  };

  function fill() {
    level.textContent = `LV ${state.level}`;
    barFill.style.width = statsFill.style.width = `${Math.round((100 * state.xpInto) / state.xpNext)}%`;
    xpText.textContent = `${state.xpInto} / ${state.xpNext} xp`;
    const risk = state.mood === "worried";
    streak.textContent = `${state.streak} day streak${risk ? "  !" : ""}`;
    streak.style.color = risk ? c.warn : c.ink;
    const hit = state.today >= state.goal;
    today.textContent = `today ${state.today} / ${state.goal}${hit ? "  ✓" : ""}`;
    mood.textContent = `${state.mood}  ${MOOD_SCORE[state.mood]}/10`;
    statsBody.textContent = `today  ${state.today} of ${state.goal}${hit ? "   goal hit" : ""}\n` +
      `commits ${state.commits}   PRs ${state.prs}   issues ${state.issues}\n` +
      `reviews ${state.reviews}   other ${state.other}\n` +
      `today's xp  ${state.todayXp}\n` +
      `streak  ${state.streak} days${risk ? "   at risk" : ""}\n` +
      `level ${state.level}   ${state.xpInto} / ${state.xpNext} xp   (${state.xp} total)\n` +
      `mood  ${state.mood}  ${MOOD_SCORE[state.mood]}/10`;
    playBody.textContent = `happiness  ${state.happiness} / 100\n` +
      `snacks  ${state.snacks} left   ${state.eaten} eaten today`;
    stage.setAttribute("aria-label", `${pack.name}, the desk pet, ${state.sleeping ? "asleep" : state.mood}. ` +
      `Level ${state.level}, a ${state.streak} day streak, ${state.today} of ${state.goal} contributions today.`);
  }

  function tick() {
    const d = new Date();
    clock.textContent = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  function showNotice() {
    if (!state.queue.length) {
      bubble.hidden = true;
      draw(shown.frame, shown.dx, shown.dy);
      return;
    }
    const n = state.queue[state.pos], color = SOURCE_COLOR[n.source] || OTHER_COLOR;
    who.textContent = n.session;
    who.style.color = color;
    what.textContent = `${n.source} ${KIND_TEXT[n.kind] || n.kind}`;
    what.style.color = n.kind === "error" ? c.warn : c.ink;
    title.textContent = n.title;
    count.textContent = `${state.pos + 1} of ${state.queue.length} waiting. Tap here for the next`;   /* 1 = newest */
    count.hidden = state.queue.length < 2;
    bubble.style.borderColor = color;
    bubble.hidden = false;
    draw(shown.frame, shown.dx, shown.dy);
  }

  function sessionWaits() {
    const n = SESSIONS[state.next++ % SESSIONS.length];
    state.queue = [n, ...state.queue.filter((q) => q.session !== n.session)];   /* the bridge lists newest first */
    state.pos = 0;
    hideStats();
    showNotice();
    player.play("notice");
    say(`${n.session}: ${n.source} ${KIND_TEXT[n.kind]}`);
  }

  const pending = () => state.queue.length > 0 || !banner.hidden || !stats.hidden;
  function ack() {   /* a tap (main.c): notice_tap acknowledges the session shown, face_ack hides banner and stats */
    if (state.queue.length) {
      const n = state.queue.splice(state.pos, 1)[0];
      if (state.pos >= state.queue.length) state.pos = 0;   /* else pos now names the next older one */
      showNotice();
      say(`${n.session} acknowledged`);
    }
    cancel(bannerTimer);
    banner.hidden = true;
    hideStats();
  }

  function petAt(x, y) {
    if (hearts.childElementCount < MAX_HEARTS) {
      const h = art(hearts, "pet-demo__heart", HEART_SCALE);
      const f = pack.heart[state.hearts++ % pack.heart.length];
      paint(h, pack.frames[f.img], c.accent);
      h.style.left = `${x - (W * HEART_SCALE) / 2}px`;
      h.style.top = `${y - (H * HEART_SCALE) / 2}px`;
      const rise = h.animate([{ transform: "translateY(0)", opacity: 1 },
        { transform: `translateY(${still ? 0 : -160}px)`, opacity: 0 }], { duration: 1100, easing: "ease-out" });
      rise.onfinish = () => h.remove();
    }
    player.pet();
    const gain = Math.max(0, HAPPY_PET - Math.floor(state.strokes / PET_FADE));   /* play.c play_pet */
    if (state.strokes < HAPPY_PET * PET_FADE) state.strokes++;
    state.happiness = Math.min(100, state.happiness + gain);
    fill();
  }

  let falling = null;
  function feed() {   /* face_feed: one fish at a time; the snack counts as it starts to fall */
    if (falling) {
      showBanner("Still eating...", c.ink);
      return;
    }
    if (state.snacks <= 0) {
      showBanner("No snacks left today", c.warn);
      return;
    }
    state.snacks--;
    state.eaten++;
    state.happiness = Math.min(100, state.happiness + HAPPY_FEED);
    fill();
    hideStats();
    const w = W * SNACK_SCALE, h = H * SNACK_SCALE;
    snack.style.left = `${X + (W * SCALE) / 2 - w / 2}px`;
    snack.hidden = false;
    let k = 0;
    const flap = () => {
      const f = pack.snack[k++ % pack.snack.length];
      paint(snack, pack.frames[f.img], c.ink);
      flapTimer = later(flap, f.ms);
    };
    let flapTimer = 0;
    flap();
    falling = snack.animate([{ transform: `translateY(${-h}px)` },
      { transform: `translateY(${Y + (H * SCALE) / 3 - h / 2}px)` }], { duration: still ? 0 : 900, easing: "ease-in" });
    falling.onfinish = () => {
      cancel(flapTimer);
      snack.hidden = true;
      falling = null;
      if (destroyed) return;
      player.play("celebrate");
      showBanner(`Yum!  ${state.snacks} snack${state.snacks === 1 ? "" : "s"} left`, c.good);
      fill();
    };
  }

  function merged() {   /* face_merged */
    state.happiness = Math.min(100, state.happiness + HAPPY_MERGE);
    hideStats();
    player.play("celebrate");
    showBanner(`Merged #${++state.merged}!`, c.good);
    fill();
  }

  function toggleStats() {
    if (!stats.hidden) {
      hideStats();
      return;
    }
    stats.hidden = false;
    statsTimer = later(() => (stats.hidden = true), STATS_MS);
  }

  function setSleeping(sleeping) {
    state.sleeping = sleeping;
    night.textContent = sleeping ? "Day" : "Night";
    screen.classList.toggle("is-night", sleeping);
    player.setSleeping(sleeping);
    draw(shown.frame, shown.dx, shown.dy);
    fill();
    say(sleeping ? `${pack.name} is asleep` : `${pack.name} is awake`);
  }

  function setMood(m) {
    state.mood = m;
    moodPick.value = m;
    player.setMood(m);
    fill();
  }

  /* Touch, as main.c: a press that starts on the pet strokes it (a heart for every STROKE_PX of travel over the pet,
   * counted as |dx| + |dy| in steps of at least JITTER_PX) and is never a swipe. A press that starts off the pet and
   * travels SWIPE_PX up or down toggles the stats. A tap (under TAP_PX of travel) on the bubble shows the next session
   * when several wait; any other tap acknowledges. One difference: a tap on the pet with nothing to acknowledge gives a
   * heart here, so a visitor's first tap does something. */
  const point = (e) => {
    const r = stage.getBoundingClientRect(), k = r.width / 720;
    return { x: (e.clientX - r.left) / k, y: (e.clientY - r.top) / k };
  };
  let press = null;
  const down = (e) => {
    if (e.button > 0) return;
    const p = point(e);
    press = { start: p, last: p, travel: 0, stroke: 0, onPet: onPet(p), petted: false };
    screen.setPointerCapture(e.pointerId);
  };
  const move = (e) => {
    if (!press || !press.onPet) return;
    const p = point(e), step = Math.abs(p.x - press.last.x) + Math.abs(p.y - press.last.y);
    if (step < JITTER_PX) return;
    if (onPet(p)) press.stroke += step;
    press.travel += step;
    press.last = p;
    if (press.stroke >= STROKE_PX) {
      press.stroke = 0;
      press.petted = true;
      petAt(p.x, p.y);
    }
  };
  const up = (e) => {
    if (!press) return;
    const p = point(e), dx = p.x - press.start.x, dy = p.y - press.start.y, was = press;
    press = null;
    if (was.onPet) {
      if (was.petted || was.travel >= TAP_PX) return;   /* petting acknowledges nothing */
      if (pending()) ack();
      else petAt(p.x, p.y);
      return;
    }
    const far = Math.abs(dx) + Math.abs(dy);
    if (far < TAP_PX) {
      if (state.queue.length > 1 && bubbleHit(p)) {
        state.pos = (state.pos + 1) % state.queue.length;
        showNotice();
      } else {
        ack();
      }
    } else if (Math.abs(dy) >= SWIPE_PX && Math.abs(dy) > Math.abs(dx)) {
      toggleStats();
    } else if (Math.abs(dx) >= SWIPE_PX) {
      status.textContent = "On the box, a sideways swipe opens the list";
      later(() => (status.textContent = ""), BANNER_MS);
    }
  };
  const lost = () => {
    press = null;
  };
  screen.addEventListener("pointerdown", down);
  screen.addEventListener("pointermove", move);
  screen.addEventListener("pointerup", up);
  screen.addEventListener("pointercancel", lost);

  /* Scale the 720x720 stage to the width it is given. */
  const fit = () => root.style.setProperty("--pd-scale", String(screen.clientWidth / 720));
  const resize = new ResizeObserver(fit);
  resize.observe(screen);
  fit();

  fill();
  tick();
  const clockTimer = setInterval(tick, 10000);
  player.rest();

  const handle = {
    destroy() {
      if (destroyed) return;
      destroyed = true;
      player.stop();
      falling?.cancel();
      for (const a of root.getAnimations({ subtree: true })) a.cancel();
      clearInterval(clockTimer);
      for (const t of timers) clearTimeout(t);
      resize.disconnect();
      root.replaceChildren();
      root.classList.remove("pet-demo");
      for (const k of Object.keys(c)) root.style.removeProperty(`--pd-${k.replace("_", "-")}`);
      if (mounts.get(root)?.token === token) mounts.delete(root);
    },
  };
  mounts.set(root, { token, destroy: handle.destroy });
  return handle;
}
