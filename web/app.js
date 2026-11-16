// 開示レーダー PWA: フィード表示・通知設定・端末登録
import { initializeApp } from "https://www.gstatic.com/firebasejs/11.0.2/firebase-app.js";
import {
  getAuth, GoogleAuthProvider, signInWithPopup, signInWithRedirect, getRedirectResult,
  onAuthStateChanged, signOut,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-auth.js";
import {
  getFirestore, collection, query, where, getDocs, doc, getDoc, setDoc, deleteDoc, serverTimestamp,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-firestore.js";
import { getMessaging, getToken, isSupported } from "https://www.gstatic.com/firebasejs/11.0.2/firebase-messaging.js";
import { firebaseConfig, vapidKey } from "./config.js";

const TYPES = [
  ["buyback", "自社株買い"], ["tob", "TOB・再編"], ["risk", "上場廃止・不正・継続疑義"],
  ["forecast_revision", "業績予想の修正"], ["forecast_gap", "予想と実績の差異"], ["dividend", "配当の変更"],
  ["split", "株式分割"], ["yutai", "株主優待"], ["dilution", "増資・売出し・希薄化"],
  ["alliance", "資本業務提携"], ["partner", "業務提携"], ["order", "受注"],
  ["special_gain", "特別利益"], ["special_loss", "特別損失・減損"], ["midterm_plan", "中期経営計画"],
  ["earnings", "決算短信"],
];
const SIZE_JA = { large: "大型", mid: "中型", small: "小型", micro: "超小型", other: "ETF等", "": "—" };
const ARROW = { up: "↑", down: "↓", "?": "" };
const DEFAULT_SETTINGS = { enabled: true, min_importance: 3, sizes: ["small", "micro"], types: [], watchlist: [], watch_min_importance: 1 };

const $ = (id) => document.getElementById(id);
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);
let user = null;
let settings = { ...DEFAULT_SETTINGS };
let events = [];

// ---- 日付（日本時間） ----
const todayJst = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
const shiftDay = (d, n) => {
  const t = new Date(d + "T00:00:00Z"); t.setUTCDate(t.getUTCDate() + n);
  return t.toISOString().slice(0, 10);
};
const params = new URLSearchParams(location.search);
$("day").value = params.get("day") || todayJst();

// ---- 画面切替 ----
function show(view) {
  $("feed").hidden = view !== "feed";
  $("settings").hidden = view !== "settings";
  $("tab-feed").classList.toggle("is-on", view === "feed");
  $("tab-settings").classList.toggle("is-on", view === "settings");
}
$("tab-feed").onclick = () => show("feed");
$("tab-settings").onclick = () => show("settings");

// ---- ログイン ----
const provider = new GoogleAuthProvider();
const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
$("btn-signin").onclick = async () => {
  try {
    // ホーム画面から開いた PWA ではポップアップが使えない環境があるためリダイレクトにする
    if (standalone) await signInWithRedirect(auth, provider);
    else await signInWithPopup(auth, provider);
  } catch (e) { $("signin-msg").textContent = `ログインに失敗: ${e.code || e.message}`; }
};
getRedirectResult(auth).catch((e) => { $("signin-msg").textContent = `ログインに失敗: ${e.code || e.message}`; });
$("btn-signout").onclick = () => signOut(auth);

onAuthStateChanged(auth, async (u) => {
  user = u;
  $("signin").hidden = !!u;
  document.querySelector(".bar nav").hidden = !u;
  if (!u) { $("feed").hidden = true; $("settings").hidden = true; return; }
  const member = await getDoc(doc(db, "members", u.uid)).catch(() => null);
  if (!member || !member.exists()) {
    $("signin").hidden = false;
    $("signin-msg").textContent = `このアカウントは未登録です（UID: ${u.uid}）。管理者に登録を依頼してください。`;
    return;
  }
  await loadSettings();
  show("feed");
  await loadDay();
  await refreshPushStatus();
});

// ---- フィード ----
for (const [v, l] of TYPES) $("f-type").add(new Option(l, v));
$("prev").onclick = () => { $("day").value = shiftDay($("day").value, -1); loadDay(); };
$("next").onclick = () => { $("day").value = shiftDay($("day").value, 1); loadDay(); };
$("day").onchange = loadDay;
for (const id of ["f-imp", "f-size", "f-type"]) $(id).onchange = render;

async function loadDay() {
  const day = $("day").value;
  $("feed-meta").textContent = "読み込み中…";
  const snap = await getDocs(query(collection(db, "events"), where("day", "==", day)));
  events = snap.docs.map((d) => ({ id: d.id, ...d.data() }));
  history.replaceState(null, "", `?day=${day}`);
  render();
}

function render() {
  const minImp = Number($("f-imp").value);
  const sizeMode = $("f-size").value;
  const type = $("f-type").value;
  const watch = new Set(settings.watchlist);
  const visible = (e) => {
    if (sizeMode === "watch") return watch.has(e.code); // ウォッチ銘柄は重要度に関係なく全件
    if (e.importance < minImp) return false;
    return sizeMode === "all" || ["small", "micro"].includes(e.size) || watch.has(e.code);
  };
  const rows = events
    .filter(visible)
    .filter((e) => !type || e.type === type)
    .sort((a, b) => (watch.has(b.code) - watch.has(a.code)) || (b.importance - a.importance) || b.published_at.localeCompare(a.published_at));
  $("feed-meta").textContent = `${$("day").value}　表示 ${rows.length}件 ／ 全イベント ${events.length}件`;
  const list = $("list");
  list.replaceChildren();
  if (!rows.length) {
    const li = document.createElement("li"); li.className = "empty"; li.textContent = "該当する開示はありません";
    list.append(li); return;
  }
  const focus = params.get("event");
  for (const e of rows) list.append(card(e, watch.has(e.code), e.id === focus));
  const f = list.querySelector(".focus");
  if (f) f.scrollIntoView({ block: "center" });
}

function card(e, watched, focus) {
  const li = document.createElement("li");
  li.className = `item imp${e.importance}${watched ? " watch" : ""}${focus ? " focus" : ""}`;
  const head = document.createElement("div"); head.className = "head";
  const span = (cls, text) => { const s = document.createElement("span"); s.className = cls; s.textContent = text; return s; };
  head.append(
    span("stars", "★".repeat(e.importance)),
    span("time", e.published_at.slice(11)),
    span("code", e.code),
    span("name", e.company_name),
    span("badge", SIZE_JA[e.size] ?? "—"),
    span(`label ${e.direction}`, `${ARROW[e.direction] || ""}${e.label}`),
  );
  if (e.profit_rate != null) head.append(span("rate", `営業益${e.profit_rate > 0 ? "+" : ""}${Math.round(e.profit_rate)}%`));
  if (watched) head.append(span("badge", "ウォッチ"));
  const a = document.createElement("a");
  a.className = "title"; a.href = e.pdf_url; a.target = "_blank"; a.rel = "noopener"; a.textContent = e.title;
  li.append(head, a);
  if (e.hist) {
    const h = document.createElement("div"); h.className = "hist";
    h.textContent = `過去${e.hist.n}件の平均的な動き: 初動 ${fmt(e.hist.car0)} → その後20日 ${fmt(e.hist.d20)}（市場・同規模比）`;
    li.append(h);
  }
  return li;
}
const fmt = (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;

// ---- 通知条件 ----
const typeBox = $("s-types");
for (const [v, l] of TYPES) {
  const lab = document.createElement("label");
  lab.innerHTML = `<input type="checkbox" name="type" value="${v}"> `;
  lab.append(l);
  typeBox.append(lab);
}

async function loadSettings() {
  const snap = await getDoc(doc(db, "settings", user.uid));
  settings = { ...DEFAULT_SETTINGS, ...(snap.exists() ? snap.data() : {}) };
  $("s-enabled").checked = settings.enabled;
  $("s-min").value = String(settings.min_importance);
  $("s-wmin").value = String(settings.watch_min_importance);
  $("s-watch").value = settings.watchlist.join(" ");
  for (const c of document.querySelectorAll("input[name=size]")) c.checked = settings.sizes.includes(c.value);
  for (const c of document.querySelectorAll("input[name=type]")) c.checked = settings.types.includes(c.value);
}

$("form-settings").onsubmit = async (ev) => {
  ev.preventDefault();
  const checked = (name) => [...document.querySelectorAll(`input[name=${name}]:checked`)].map((c) => c.value);
  settings = {
    enabled: $("s-enabled").checked,
    min_importance: Number($("s-min").value),
    watch_min_importance: Number($("s-wmin").value),
    sizes: checked("size"),
    types: checked("type"),
    watchlist: [...new Set($("s-watch").value.toUpperCase().split(/[\s,、]+/).filter((c) => /^[0-9A-Z]{4}$/.test(c)))],
  };
  await setDoc(doc(db, "settings", user.uid), { ...settings, updated_at: serverTimestamp() });
  $("s-watch").value = settings.watchlist.join(" ");
  $("save-msg").textContent = "保存した";
  setTimeout(() => ($("save-msg").textContent = ""), 2500);
  render();
};

// ---- 端末の通知登録 ----
async function swRegistration() {
  const q = new URLSearchParams({
    apiKey: firebaseConfig.apiKey, projectId: firebaseConfig.projectId,
    messagingSenderId: firebaseConfig.messagingSenderId, appId: firebaseConfig.appId,
  });
  return navigator.serviceWorker.register(`/firebase-messaging-sw.js?${q}`);
}

async function refreshPushStatus() {
  if (!(await isSupported())) {
    $("push-status").textContent = standalone ? "この端末は通知に対応していません。" : "このブラウザでは通知を使えません（iPhone はホーム画面に追加して開いてください）。";
    $("btn-push").hidden = true;
    return;
  }
  const token = localStorage.getItem("radar.token");
  const on = Notification.permission === "granted" && token;
  // 通知を受けている端末では起動のたびに Service Worker の更新を確認する
  if (on) swRegistration().then((r) => r.update()).catch(() => {});
  $("push-status").textContent = on ? "この端末は通知を受け取る設定になっています。" : "この端末の通知はオフです。";
  $("btn-push").textContent = on ? "この端末の通知をオフにする" : "通知を有効にする";
  $("btn-push").onclick = on ? disablePush : enablePush;
}

async function enablePush() {
  try {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") { $("push-status").textContent = "通知が許可されませんでした。"; return; }
    const reg = await swRegistration();
    // vapidKey が空なら Firebase 既定の鍵が使われる（独自の鍵ペアは不要）
    const opts = { serviceWorkerRegistration: reg, ...(vapidKey ? { vapidKey } : {}) };
    const token = await getToken(getMessaging(app), opts);
    await setDoc(doc(db, "devices", token), { uid: user.uid, ua: navigator.userAgent.slice(0, 120), created_at: serverTimestamp() });
    localStorage.setItem("radar.token", token);
  } catch (e) {
    $("push-status").textContent = `登録に失敗: ${e.code || e.message}`;
    return;
  }
  await refreshPushStatus();
}

async function disablePush() {
  const token = localStorage.getItem("radar.token");
  if (token) await deleteDoc(doc(db, "devices", token)).catch(() => {});
  localStorage.removeItem("radar.token");
  await refreshPushStatus();
}

// 通知の表示は Service Worker が必ず行う。開いている間に届いたらフィードを再読み込みする
navigator.serviceWorker?.addEventListener("message", (e) => {
  if (e.data && e.data.type === "radar-push" && user) loadDay();
});
