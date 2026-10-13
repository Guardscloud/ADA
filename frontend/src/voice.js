// Sesli kullanım: konuşarak yazma (Web Speech API varsa) ve yanıtları sesli okuma.

export function recognitionSupported() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

/** Dinlemeyi başlatır; tanınan metni onText ile verir. Durdurmak için dönen fonksiyonu çağırın. */
export function listen(onText, onEnd) {
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new Rec();
  rec.lang = "tr-TR";
  rec.interimResults = true;
  rec.continuous = false;
  rec.onresult = (e) => onText(Array.from(e.results).map((r) => r[0].transcript).join(""), e.results[e.results.length - 1].isFinal);
  rec.onend = onEnd;
  rec.onerror = onEnd;
  rec.start();
  return () => rec.stop();
}

/** Markdown'u sadeleştirip ilk birkaç cümleyi Türkçe sesle okur. */
export function speak(markdown) {
  if (!("speechSynthesis" in window) || !markdown) return;
  const plain = markdown
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[#*_`>|]/g, " ")
    .replace(/\[(.*?)\]\(.*?\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 400);
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(plain);
  u.lang = "tr-TR";
  const tr = window.speechSynthesis.getVoices().find((v) => v.lang?.startsWith("tr"));
  if (tr) u.voice = tr;
  window.speechSynthesis.speak(u);
}
