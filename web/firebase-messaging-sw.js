// プッシュ通知の受信。Firebase SDK の自動表示には頼らず、届いたら必ず自分で表示する。
// 理由: SDK はアプリを開いている間は通知を出さずページに渡すため何も表示されない。
//       また iPhone は通知を表示しないプッシュが続くと購読を取り消す。
// （登録時のクエリに Firebase 設定が付くが、このファイルでは使わない）

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let p = {};
  try { p = event.data ? event.data.json() : {}; } catch (_) { p = {}; }
  // FCM の Web Push は {notification:{title,body,tag,icon}, data:{url}, fcmOptions:{link}} の形で届く
  const n = p.notification || {};
  const data = p.data || {};
  const url = data.url || (p.fcmOptions && p.fcmOptions.link) || "/";
  const title = n.title || data.title || "開示レーダー";
  event.waitUntil((async () => {
    await self.registration.showNotification(title, {
      body: n.body || data.body || "",
      tag: n.tag || data.tag || undefined,
      icon: n.icon || "/icons/icon-192.png",
      data: { url },
    });
    // 開いている画面にはフィードの再読み込みを知らせる
    const list = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of list) c.postMessage({ type: "radar-push" });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil((async () => {
    const list = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of list) {
      if ("focus" in c) { await c.focus(); return c.navigate(url).catch(() => {}); }
    }
    return self.clients.openWindow(url);
  })());
});
