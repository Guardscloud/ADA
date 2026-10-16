import { Globe } from "lucide-react";
import { LANGS, currentLang, setLang } from "../i18n";

// Arayüz dili seçici. Dil adları kendi dillerinde yazılır, çevrilmez.
// Dil seçilmemişse İngilizce görünür; seçilen dil kaydedilir.
export default function LanguageSelect({ className = "" }) {
  return (
    <label className={"lang-select " + className} data-no-i18n title="Language / Dil">
      <Globe size={15} />
      <select value={currentLang()} onChange={(e) => setLang(e.target.value)} aria-label="Language">
        {Object.entries(LANGS).map(([code, name]) => (
          <option key={code} value={code}>
            {name}
          </option>
        ))}
      </select>
    </label>
  );
}
