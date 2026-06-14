importScripts("https://www.gstatic.com/firebasejs/10.12.5/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.12.5/firebase-messaging-compat.js");

firebase.initializeApp({
  apiKey: "AIzaSyBOvQlxIZT2cB5bIvNTZOu-OsN7zzIWjV4",
  authDomain: "fagni-c2eb9.firebaseapp.com",
  projectId: "fagni-c2eb9",
  storageBucket: "fagni-c2eb9.firebasestorage.app",
  messagingSenderId: "1006320008190",
  appId: "1:1006320008190:web:dbf3503aba71b174e9deaf",
  measurementId: "G-EWGC5P6N6E"
});

const messaging = firebase.messaging();

messaging.onBackgroundMessage(function(payload) {
  const title = payload.notification?.title || "FAGNI";
  const options = {
    body: payload.notification?.body || "Nouvelle notification",
    icon: "/static/favicon.png",
    data: payload.data || {}
  };
  self.registration.showNotification(title, options);
});
