// Tarayıcı hafızası (localStorage) bazı gömülü tarayıcılarda (ör. uygulama penceresi) erişimi
// reddedebilir; tüm erişimler buradan geçer ve hata uygulamayı durdurmaz.

export function getItem(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function setItem(key, value) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* depolama kullanılamıyor: tercih bu oturumla sınırlı kalır */
  }
}
