// プッシュ通知の受信（バックグラウンド）。FCM は通知を自動表示するため、ここではクリック時の遷移だけ扱う
importScripts("https://www.gstatic.com/firebasejs/11.0.2/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/11.0.2/firebase-messaging-compat.js");

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const url = (data.FCM_MSG && data.FCM_MSG.data && data.FCM_MSG.data.url) || data.url || "/";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if ("focus" in c) { c.navigate(url); return c.focus(); }
      }
      return clients.openWindow(url);
    })
  );
});

// 設定値は app.js 側から登録時のクエリで受け取る（このファイルは静的に配信するため）
const params = new URL(self.location).searchParams;
if (params.get("projectId")) {
  firebase.initializeApp({
    apiKey: params.get("apiKey"),
    projectId: params.get("projectId"),
    messagingSenderId: params.get("messagingSenderId"),
    appId: params.get("appId"),
  });
  firebase.messaging();
}
