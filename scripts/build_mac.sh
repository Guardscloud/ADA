#!/usr/bin/env bash
# ADA.app üretir (macOS). Çıktı: dist/ADA.app
# Kullanım: scripts/build_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# Arayüz derlenmiş değilse derle
if [ ! -f ada/static/dist/index.html ]; then
  (cd frontend && npm install && npm run build)
fi

uv run pyinstaller \
  --noconfirm --clean --windowed \
  --name ADA \
  --osx-bundle-identifier com.ada-agi.ada \
  --icon assets/icon.icns \
  --add-data "ada/static/dist:ada/static/dist" \
  --collect-submodules ada \
  --collect-all ddgs \
  --collect-all primp \
  --collect-all trafilatura \
  --collect-all justext \
  --collect-data docx \
  --collect-data pptx \
  --collect-submodules google.genai \
  --collect-submodules uvicorn \
  --hidden-import pyautogui \
  app_main.py

# macOS izin açıklamaları (Ekran Kaydı / Otomasyon / Erişilebilirlik istemlerinde görünür)
PLIST=dist/ADA.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string 'ADA, Mail, Takvim ve Notlar gibi uygulamaları sizin adınıza kullanmak için izin ister.'" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSScreenCaptureUsageDescription string 'ADA, ekrandaki işleri yapabilmek için ekran görüntüsü alır.'" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 12.0" "$PLIST" 2>/dev/null || true

# İmza: sabit bir kimlik (Apple Development / Developer ID) varsa onu kullan. Ad-hoc imza ("-") her
# derlemede yeni kimlik üretir ve macOS izinleri (Otomasyon, Ekran Kaydı) her güncellemede sıfırlanır.
IDENTITY="${ADA_SIGN_IDENTITY:-}"
if [ -z "$IDENTITY" ]; then
  IDENTITY=$(security find-identity -v -p codesigning 2>/dev/null \
    | grep -E '"(Developer ID Application|Apple Development)' | head -1 | awk '{print $2}')
fi
if [ -n "$IDENTITY" ]; then
  echo "İmzalanıyor: $(security find-identity -v -p codesigning | grep "$IDENTITY" | sed 's/.*"\(.*\)"/\1/')"
  if security find-identity -v -p codesigning | grep "$IDENTITY" | grep -q "Developer ID Application"; then
    # Dağıtım imzası: hardened runtime + zaman damgası (notarization için zorunlu)
    codesign --force --deep --options runtime --timestamp --entitlements scripts/entitlements.plist --sign "$IDENTITY" dist/ADA.app
  else
    codesign --force --deep --timestamp=none --sign "$IDENTITY" dist/ADA.app
  fi
else
  echo "UYARI: kod imzalama kimliği yok; ad-hoc imza kullanılıyor (izinler her güncellemede sıfırlanır)."
  codesign --force --deep --sign - dist/ADA.app
fi
codesign --verify --deep --strict dist/ADA.app

# Apple onayı (notarization): başkalarının "tanınmayan geliştirici" uyarısı almadan açabilmesi için.
# Gereken: Developer ID Application sertifikası + bir kez:
#   xcrun notarytool store-credentials ADA_NOTARY --apple-id <e-posta> --team-id <takım> --password <uygulamaya özel parola>
if [ -n "${ADA_NOTARY_PROFILE:-}" ]; then
  ditto -c -k --keepParent dist/ADA.app dist/ADA.zip
  xcrun notarytool submit dist/ADA.zip --keychain-profile "$ADA_NOTARY_PROFILE" --wait
  xcrun stapler staple dist/ADA.app
  rm -f dist/ADA.zip
  ditto -c -k --keepParent dist/ADA.app dist/ADA-mac.zip
  echo "Onaylandı ve paketlendi: dist/ADA-mac.zip (dağıtıma hazır)"
fi

echo "Hazır: dist/ADA.app  ($(du -sh dist/ADA.app | cut -f1))"
