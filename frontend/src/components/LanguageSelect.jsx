import { Globe } from "lucide-react";
import { LANGS, currentLang, langSetting, setLang } from "../i18n";

// Arayüz dili seçici. Dil adları kendi dillerinde yazılır, çevrilmez.
export default function LanguageSelect({ className = "" }) {
  const auto = langSetting() === "auto";
  return (
    <label className={"lang-select " + className} data-no-i18n title="Language / Dil">
      <Globe size={15} />
      <select value={auto ? "auto" : currentLang()} onChange={(e) => setLang(e.target.value)} aria-label="Language">
        <option value="auto">Auto ({LANGS[currentLang()]})</option>
        {Object.entries(LANGS).map(([code, name]) => (
          <option key={code} value={code}>
            {name}
          </option>
        ))}
      </select>
    </label>
  );
}
