// UI chrome strings. Match narration itself comes from the engine in the fan's language.

export const LANGUAGES = ["en", "es", "fr", "pt", "ar", "hi"] as const;
export type Lang = (typeof LANGUAGES)[number];

const STRINGS = {
  en: { live: "Live", feed: "Match feed", winProb: "Win probability", possession: "Possession", xg: "xG", shots: "Shots", draw: "Draw", waiting: "Waiting for kick-off…", reveal: "Tap to reveal", ai: "AI", fullTime: "Full time", connecting: "Connecting…", reconnecting: "Reconnecting…", momentum: "Momentum", control: "Control", chaos: "Chaos", whyItMatters: "Why it matters" },
  es: { live: "En directo", feed: "Lo último", winProb: "Probabilidad de victoria", possession: "Posesión", xg: "xG", shots: "Tiros", draw: "Empate", waiting: "Esperando el saque inicial…", reveal: "Toca para ver", ai: "IA", fullTime: "Final", connecting: "Conectando…", reconnecting: "Reconectando…", momentum: "Dominio", control: "Control", chaos: "Caos", whyItMatters: "Por qué importa" },
  fr: { live: "En direct", feed: "Le fil du match", winProb: "Probabilité de victoire", possession: "Possession", xg: "xG", shots: "Tirs", draw: "Nul", waiting: "En attente du coup d'envoi…", reveal: "Touchez pour voir", ai: "IA", fullTime: "Fin du match", connecting: "Connexion…", reconnecting: "Reconnexion…", momentum: "Dynamique", control: "Contrôle", chaos: "Désordre", whyItMatters: "Pourquoi c'est important" },
  pt: { live: "Ao vivo", feed: "Lance a lance", winProb: "Probabilidade de vitória", possession: "Posse", xg: "xG", shots: "Chutes", draw: "Empate", waiting: "Aguardando o pontapé inicial…", reveal: "Toque para ver", ai: "IA", fullTime: "Fim de jogo", connecting: "Conectando…", reconnecting: "Reconectando…", momentum: "Domínio", control: "Controle", chaos: "Caos", whyItMatters: "Por que importa" },
  ar: { live: "مباشر", feed: "مجريات المباراة", winProb: "احتمال الفوز", possession: "الاستحواذ", xg: "xG", shots: "التسديدات", draw: "تعادل", waiting: "بانتظار صافرة البداية…", reveal: "اضغط للعرض", ai: "ذكاء اصطناعي", fullTime: "نهاية المباراة", connecting: "جارٍ الاتصال…", reconnecting: "إعادة الاتصال…", momentum: "الأفضلية", control: "السيطرة", chaos: "الفوضى", whyItMatters: "لماذا يهم" },
  hi: { live: "लाइव", feed: "मैच अपडेट", winProb: "जीत की संभावना", possession: "कब्ज़ा", xg: "xG", shots: "शॉट", draw: "ड्रॉ", waiting: "किक-ऑफ़ का इंतज़ार…", reveal: "देखने के लिए टैप करें", ai: "AI", fullTime: "फ़ुल टाइम", connecting: "कनेक्ट हो रहा है…", reconnecting: "फिर से कनेक्ट हो रहा है…", momentum: "मोमेंटम", control: "नियंत्रण", chaos: "अफ़रा-तफ़री", whyItMatters: "यह क्यों अहम है" },
} as const satisfies Record<Lang, Record<string, string>>;

export type StringKey = keyof (typeof STRINGS)["en"];

export function isLang(value: string | null | undefined): value is Lang {
  return !!value && (LANGUAGES as readonly string[]).includes(value);
}

export function t(lang: string | null | undefined, key: StringKey): string {
  return STRINGS[isLang(lang) ? lang : "en"][key];
}

export function dir(lang: string | null | undefined): "rtl" | "ltr" {
  return lang === "ar" ? "rtl" : "ltr";
}

// Feed labels for insight types (keys match the engine's InsightType).
const TYPE_LABELS: Record<Lang, Record<string, string>> = {
  en: { goal: "Goal", big_chance: "Big chance", xg_swing: "Chance quality", win_prob_swing: "Turning point", momentum_shift: "Momentum", pressure_change: "Pressing", control_chaos_flip: "Control vs chaos", rhythm_break: "Tempo", pass_of_note: "Pass of note", shot_speed_record: "Shot speed", sprint_threshold: "Top speed", distance_milestone: "Work rate", career_milestone: "Milestone", player_streak: "On a roll", tactical_note: "Tactics", fatigue_alert: "Fatigue", duel: "Duel", card: "Card" },
  es: { goal: "Gol", big_chance: "Ocasión clara", xg_swing: "Calidad de ocasiones", win_prob_swing: "Momento clave", momentum_shift: "Dominio", pressure_change: "Presión", control_chaos_flip: "Control o caos", rhythm_break: "Ritmo", pass_of_note: "Gran pase", shot_speed_record: "Velocidad de disparo", sprint_threshold: "Velocidad punta", distance_milestone: "Despliegue físico", career_milestone: "Hito", player_streak: "En racha", tactical_note: "Táctica", fatigue_alert: "Cansancio", duel: "Duelo", card: "Tarjeta" },
  fr: { goal: "But", big_chance: "Grosse occasion", xg_swing: "Qualité des occasions", win_prob_swing: "Tournant", momentum_shift: "Dynamique", pressure_change: "Pressing", control_chaos_flip: "Contrôle ou désordre", rhythm_break: "Rythme", pass_of_note: "Passe remarquable", shot_speed_record: "Vitesse de frappe", sprint_threshold: "Vitesse de pointe", distance_milestone: "Volume de course", career_milestone: "Cap", player_streak: "En série", tactical_note: "Tactique", fatigue_alert: "Fatigue", duel: "Duel", card: "Carton" },
  pt: { goal: "Gol", big_chance: "Chance clara", xg_swing: "Qualidade das chances", win_prob_swing: "Momento decisivo", momentum_shift: "Domínio", pressure_change: "Pressão", control_chaos_flip: "Controle ou caos", rhythm_break: "Ritmo", pass_of_note: "Grande passe", shot_speed_record: "Velocidade do chute", sprint_threshold: "Velocidade máxima", distance_milestone: "Intensidade", career_milestone: "Marco", player_streak: "Em sequência", tactical_note: "Tática", fatigue_alert: "Cansaço", duel: "Duelo", card: "Cartão" },
  ar: { goal: "هدف", big_chance: "فرصة كبيرة", xg_swing: "جودة الفرص", win_prob_swing: "لحظة حاسمة", momentum_shift: "الأفضلية", pressure_change: "الضغط", control_chaos_flip: "السيطرة أم الفوضى", rhythm_break: "الإيقاع", pass_of_note: "تمريرة مميزة", shot_speed_record: "سرعة التسديد", sprint_threshold: "السرعة القصوى", distance_milestone: "المجهود البدني", career_milestone: "إنجاز", player_streak: "سلسلة مميزة", tactical_note: "تكتيك", fatigue_alert: "إرهاق", duel: "صراع ثنائي", card: "بطاقة" },
  hi: { goal: "गोल", big_chance: "बड़ा मौका", xg_swing: "मौकों की गुणवत्ता", win_prob_swing: "निर्णायक पल", momentum_shift: "मोमेंटम", pressure_change: "प्रेसिंग", control_chaos_flip: "नियंत्रण या अफ़रा-तफ़री", rhythm_break: "रफ़्तार", pass_of_note: "शानदार पास", shot_speed_record: "शॉट की रफ़्तार", sprint_threshold: "टॉप स्पीड", distance_milestone: "मेहनत", career_milestone: "उपलब्धि", player_streak: "लगातार", tactical_note: "रणनीति", fatigue_alert: "थकान", duel: "द्वंद्व", card: "कार्ड" },
};

export function typeLabel(lang: string | null | undefined, type: string): string {
  const labels = TYPE_LABELS[isLang(lang) ? lang : "en"];
  return labels[type] ?? TYPE_LABELS.en[type] ?? type.replaceAll("_", " ");
}
