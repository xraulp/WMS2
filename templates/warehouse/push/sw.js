{% load static %}// El service worker del WMS. Lo unico que hace es recibir los avisos push y
// abrir la operacion al tocarlos: no guarda paginas en cache, asi que el WMS
// se sigue cargando del servidor como siempre.
// Lo sirve `warehouse/vistas_push.py` desde la raiz, para que alcance a todas
// las pantallas.

self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; }
  catch (_) { d = { title: 'WMS', body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'WMS', {
    body: d.body || '',
    // Los avisos con el mismo tag se reemplazan: cinco mensajes seguidos del
    // mismo hilo dejan un aviso, no cinco. `renotify` hace que el nuevo suene.
    tag: d.tag || undefined,
    renotify: !!d.tag,
    icon: '{% static "warehouse/push/icono-192.png" %}',
    badge: '{% static "warehouse/push/insignia-96.png" %}',
    data: { url: d.url || '/' }
  }));
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var destino = new URL((e.notification.data && e.notification.data.url) || '/',
                        self.location.origin).href;
  // Si el WMS ya esta abierto, se usa esa ventana en vez de abrir otra.
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    .then(function (ventanas) {
      for (var i = 0; i < ventanas.length; i++) {
        var v = ventanas[i];
        if (new URL(v.url).origin === self.location.origin && 'navigate' in v) {
          return v.navigate(destino).then(function (w) { return (w || v).focus(); })
            .catch(function () { return self.clients.openWindow(destino); });
        }
      }
      return self.clients.openWindow(destino);
    }));
});
