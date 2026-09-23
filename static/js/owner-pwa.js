if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/owner/sw.js', {scope: '/owner/'}).catch(() => {});
  });
}

const installButton = document.getElementById('owner-install-button');
const installCard = document.getElementById('owner-install-card');
const iphoneHelp = document.getElementById('owner-iphone-help');
const isInstalled = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
let installPrompt;

if (iphoneHelp && !isInstalled && /iPhone|iPad|iPod/.test(navigator.userAgent)) {
  installCard.hidden = false;
  iphoneHelp.hidden = false;
}

window.addEventListener('beforeinstallprompt', event => {
  if (!installButton || isInstalled) return;
  event.preventDefault();
  installPrompt = event;
  installCard.hidden = false;
  installButton.hidden = false;
});

installButton?.addEventListener('click', async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  installButton.hidden = true;
});

window.addEventListener('appinstalled', () => {
  if (installButton) installButton.hidden = true;
  if (iphoneHelp) iphoneHelp.hidden = true;
  if (installCard) installCard.hidden = true;
});
