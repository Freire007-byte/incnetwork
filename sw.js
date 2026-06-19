const CACHE_NAME = "inc-network-v4";

const PRECACHE = [
  "./index.html",
  "./manifest.json",
  "./logo.png",
  "./logo.svg",
  "./inc-wallet.svg",
  "./ethers-5.7.umd.min.js",
  "./qrcode.min.js"
];

// Instala e pré-carrega os assets estáticos
self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

// Remove caches antigos ao ativar nova versão
self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Estratégia: Cache-first para assets locais, Network-first para RPCs e APIs externas
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);

  // Dashboard do Caca Pump — nunca interceptar, deixa browser buscar direto
  if (url.pathname.includes("dashboard")) {
    return;
  }

  // Requisições de RPC (blockchain) e APIs externas — sempre network
  if (
    url.hostname.includes("rpc") ||
    url.hostname.includes("infura") ||
    url.hostname.includes("alchemy") ||
    url.hostname.includes("arbitrum.io") ||
    url.hostname.includes("polygon.technology") ||
    url.hostname.includes("avax.network") ||
    url.hostname.includes("llamarpc.com") ||
    url.hostname.includes("publicnode.com") ||
    url.hostname.includes("counterapi.dev") ||
    url.hostname.includes("binance.com") ||
    url.hostname.includes("coingecko.com") ||
    url.hostname.includes("cryptocompare.com") ||
    url.protocol === "chrome-extension:"
  ) {
    return; // deixa o browser lidar normalmente
  }

  // Assets locais e fonts — Cache-first
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        if (!response || response.status !== 200 || response.type === "opaque") {
          return response;
        }
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        return response;
      }).catch(() => caches.match("./index.html"));
    })
  );
});
