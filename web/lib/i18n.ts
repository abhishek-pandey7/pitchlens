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

type AskStrings = {
  title: string;
  placeholder: string;
  send: string;
  thinking: string;
  failed: string;
  verified: string;
  verifiedHint: string;
  how: string;
  suggestions: string[];
};

const ASK: Record<Lang, AskStrings> = {
  en: {
    title: "Ask the match",
    placeholder: "Ask anything about this match…",
    send: "Ask",
    thinking: "Checking the match data…",
    failed: "Couldn't reach the match companion. Try again.",
    verified: "Verified",
    verifiedHint: "Every number comes from the match data and was checked by the Fact-Checker agent",
    how: "How we answered",
    suggestions: ["Who is on top right now?", "How did the pressing change after 60'?", "Who had the fastest shot?"],
  },
  es: {
    title: "Pregunta al partido",
    placeholder: "Pregunta lo que quieras sobre el partido…",
    send: "Preguntar",
    thinking: "Revisando los datos del partido…",
    failed: "No se pudo contactar con el asistente. Inténtalo de nuevo.",
    verified: "Verificado",
    verifiedHint: "Cada cifra sale de los datos del partido y la ha comprobado el agente verificador",
    how: "Cómo lo respondimos",
    suggestions: ["¿Quién domina ahora?", "¿Cómo cambió la presión tras el 60'?", "¿Quién hizo el disparo más rápido?"],
  },
  fr: {
    title: "Interrogez le match",
    placeholder: "Posez une question sur ce match…",
    send: "Demander",
    thinking: "Vérification des données du match…",
    failed: "Impossible de joindre l'assistant. Réessayez.",
    verified: "Vérifié",
    verifiedHint: "Chaque chiffre vient des données du match et a été vérifié par l'agent de vérification",
    how: "Comment nous avons répondu",
    suggestions: ["Qui domine en ce moment ?", "Comment le pressing a-t-il changé après la 60e ?", "Qui a frappé le plus fort ?"],
  },
  pt: {
    title: "Pergunte à partida",
    placeholder: "Pergunte algo sobre esta partida…",
    send: "Perguntar",
    thinking: "Verificando os dados da partida…",
    failed: "Não foi possível falar com o assistente. Tente de novo.",
    verified: "Verificado",
    verifiedHint: "Cada número vem dos dados da partida e foi checado pelo agente verificador",
    how: "Como respondemos",
    suggestions: ["Quem está melhor agora?", "Como mudou a pressão depois dos 60'?", "Quem deu o chute mais rápido?"],
  },
  ar: {
    title: "اسأل المباراة",
    placeholder: "اسأل أي شيء عن هذه المباراة…",
    send: "اسأل",
    thinking: "جارٍ التحقق من بيانات المباراة…",
    failed: "تعذّر الوصول إلى المساعد. حاول مرة أخرى.",
    verified: "تم التحقق",
    verifiedHint: "كل رقم مأخوذ من بيانات المباراة وتحقق منه وكيل التدقيق",
    how: "كيف أجبنا",
    suggestions: ["من المسيطر الآن؟", "كيف تغيّر الضغط بعد الدقيقة 60؟", "من صاحب أسرع تسديدة؟"],
  },
  hi: {
    title: "मैच से पूछें",
    placeholder: "इस मैच के बारे में कुछ भी पूछें…",
    send: "पूछें",
    thinking: "मैच का डेटा जाँचा जा रहा है…",
    failed: "सहायक से संपर्क नहीं हो सका। फिर से कोशिश करें।",
    verified: "सत्यापित",
    verifiedHint: "हर आँकड़ा मैच के डेटा से है और फ़ैक्ट-चेकर एजेंट ने उसे जाँचा है",
    how: "हमने कैसे जवाब दिया",
    suggestions: ["अभी कौन हावी है?", "60' के बाद प्रेसिंग कैसे बदली?", "सबसे तेज़ शॉट किसका था?"],
  },
};

export function askStrings(lang: string | null | undefined): AskStrings {
  return ASK[isLang(lang) ? lang : "en"];
}

type RecapStrings = {
  title: string;
  ht: string;
  ft: string;
  writing: string;
  notYet: string;
  failed: string;
  potm: string;
  standout: string;
  verified: string;
  verifiedHint: string;
  facts: string;
  how: string;
};

const RECAP: Record<Lang, RecapStrings> = {
  en: { title: "Recaps", ht: "Half-time", ft: "Full-time", writing: "The newsroom is writing your recap…", notYet: "Available after the whistle.", failed: "Couldn't get the recap. Try again.", potm: "Player of the match", standout: "Standout player", verified: "Fact-checked", verifiedHint: "Written by the Editor's agents; every number was checked against the match data", facts: "Key facts", how: "How we wrote it" },
  es: { title: "Resúmenes", ht: "Descanso", ft: "Final del partido", writing: "La redacción está escribiendo tu resumen…", notYet: "Disponible tras el pitido.", failed: "No se pudo obtener el resumen. Inténtalo de nuevo.", potm: "Jugador del partido", standout: "Jugador destacado", verified: "Verificado", verifiedHint: "Escrito por los agentes del editor; cada cifra se comprobó con los datos del partido", facts: "Datos clave", how: "Cómo lo escribimos" },
  fr: { title: "Résumés", ht: "Mi-temps", ft: "Après-match", writing: "La rédaction écrit votre résumé…", notYet: "Disponible après le coup de sifflet.", failed: "Impossible d'obtenir le résumé. Réessayez.", potm: "Homme du match", standout: "Joueur en vue", verified: "Vérifié", verifiedHint: "Écrit par les agents de la rédaction ; chaque chiffre a été vérifié avec les données du match", facts: "L'essentiel", how: "Comment nous l'avons écrit" },
  pt: { title: "Resumos", ht: "Intervalo", ft: "Pós-jogo", writing: "A redação está escrevendo seu resumo…", notYet: "Disponível após o apito.", failed: "Não foi possível obter o resumo. Tente de novo.", potm: "Melhor em campo", standout: "Destaque", verified: "Verificado", verifiedHint: "Escrito pelos agentes da redação; cada número foi checado com os dados da partida", facts: "Fatos principais", how: "Como escrevemos" },
  ar: { title: "الملخصات", ht: "الشوط الأول", ft: "المباراة كاملة", writing: "غرفة التحرير تكتب ملخصك…", notYet: "متاح بعد صافرة الحكم.", failed: "تعذّر الحصول على الملخص. حاول مرة أخرى.", potm: "رجل المباراة", standout: "اللاعب الأبرز", verified: "تم التحقق", verifiedHint: "كتبه وكلاء التحرير، وتم التحقق من كل رقم ببيانات المباراة", facts: "أبرز الحقائق", how: "كيف كتبناه" },
  hi: { title: "रीकैप", ht: "हाफ़-टाइम", ft: "फ़ुल-टाइम", writing: "न्यूज़रूम आपका रीकैप लिख रहा है…", notYet: "सीटी बजने के बाद उपलब्ध।", failed: "रीकैप नहीं मिल सका। फिर से कोशिश करें।", potm: "प्लेयर ऑफ़ द मैच", standout: "सबसे बेहतरीन खिलाड़ी", verified: "तथ्य-जाँचा गया", verifiedHint: "एडिटर के एजेंटों ने लिखा; हर आँकड़ा मैच के डेटा से जाँचा गया", facts: "मुख्य तथ्य", how: "हमने इसे कैसे लिखा" },
};

export function recapStrings(lang: string | null | undefined): RecapStrings {
  return RECAP[isLang(lang) ? lang : "en"];
}

type FocusStrings = {
  title: string; pick: string; none: string; minutes: string; touches: string; passes: string; accuracy: string;
  difficulty: string; keyPasses: string; goals: string; assists: string; shots: string; xg: string;
  tackles: string; interceptions: string; recoveries: string; distance: string; topSpeed: string; sprints: string;
  touchMap: string; attacking: string; moments: string; noMoments: string; noTracking: string; onPitch: string; off: string;
};

const FOCUS: Record<Lang, FocusStrings> = {
  en: { title: "Player focus", pick: "Follow a player", none: "Pick a player to follow their match.", minutes: "min", touches: "Touches", passes: "Passes", accuracy: "Accuracy", difficulty: "Pass difficulty", keyPasses: "Key passes", goals: "Goals", assists: "Assists", shots: "Shots", xg: "xG", tackles: "Tackles", interceptions: "Interceptions", recoveries: "Recoveries", distance: "Distance", topSpeed: "Top speed", sprints: "Sprints", touchMap: "Touch map", attacking: "attacking", moments: "Moments", noMoments: "No moments yet.", noTracking: "Running data needs tracking.", onPitch: "on the pitch", off: "off" },
  es: { title: "Jugador en foco", pick: "Sigue a un jugador", none: "Elige un jugador para seguir su partido.", minutes: "min", touches: "Toques", passes: "Pases", accuracy: "Precisión", difficulty: "Dificultad de pase", keyPasses: "Pases clave", goals: "Goles", assists: "Asistencias", shots: "Tiros", xg: "xG", tackles: "Entradas", interceptions: "Intercepciones", recoveries: "Recuperaciones", distance: "Distancia", topSpeed: "Velocidad máx.", sprints: "Sprints", touchMap: "Mapa de toques", attacking: "ataca", moments: "Momentos", noMoments: "Aún no hay momentos.", noTracking: "Los datos físicos necesitan tracking.", onPitch: "en el campo", off: "fuera" },
  fr: { title: "Joueur suivi", pick: "Suivre un joueur", none: "Choisissez un joueur pour suivre son match.", minutes: "min", touches: "Touches", passes: "Passes", accuracy: "Précision", difficulty: "Difficulté des passes", keyPasses: "Passes clés", goals: "Buts", assists: "Passes déc.", shots: "Tirs", xg: "xG", tackles: "Tacles", interceptions: "Interceptions", recoveries: "Récupérations", distance: "Distance", topSpeed: "Vitesse max.", sprints: "Sprints", touchMap: "Carte des touches", attacking: "attaque", moments: "Moments", noMoments: "Pas encore de moments.", noTracking: "Les données physiques demandent le tracking.", onPitch: "sur le terrain", off: "sorti" },
  pt: { title: "Jogador em foco", pick: "Siga um jogador", none: "Escolha um jogador para acompanhar a partida dele.", minutes: "min", touches: "Toques", passes: "Passes", accuracy: "Precisão", difficulty: "Dificuldade dos passes", keyPasses: "Passes decisivos", goals: "Gols", assists: "Assistências", shots: "Chutes", xg: "xG", tackles: "Desarmes", interceptions: "Interceptações", recoveries: "Recuperações", distance: "Distância", topSpeed: "Velocidade máx.", sprints: "Sprints", touchMap: "Mapa de toques", attacking: "ataca", moments: "Momentos", noMoments: "Ainda sem momentos.", noTracking: "Os dados físicos precisam de tracking.", onPitch: "em campo", off: "fora" },
  ar: { title: "لاعب تحت المجهر", pick: "تابع لاعبًا", none: "اختر لاعبًا لمتابعة مباراته.", minutes: "د", touches: "اللمسات", passes: "التمريرات", accuracy: "الدقة", difficulty: "صعوبة التمرير", keyPasses: "تمريرات مفتاحية", goals: "الأهداف", assists: "التمريرات الحاسمة", shots: "التسديدات", xg: "xG", tackles: "الافتكاكات", interceptions: "الاعتراضات", recoveries: "الاستعادات", distance: "المسافة", topSpeed: "السرعة القصوى", sprints: "الانطلاقات", touchMap: "خريطة اللمسات", attacking: "اتجاه الهجوم", moments: "اللحظات", noMoments: "لا لحظات بعد.", noTracking: "البيانات البدنية تحتاج إلى التتبع.", onPitch: "في الملعب", off: "خارج الملعب" },
  hi: { title: "खिलाड़ी पर फ़ोकस", pick: "किसी खिलाड़ी को फ़ॉलो करें", none: "किसी खिलाड़ी को चुनें और उसका मैच देखें।", minutes: "मिनट", touches: "टच", passes: "पास", accuracy: "सटीकता", difficulty: "पास की कठिनाई", keyPasses: "की पास", goals: "गोल", assists: "असिस्ट", shots: "शॉट", xg: "xG", tackles: "टैकल", interceptions: "इंटरसेप्शन", recoveries: "रिकवरी", distance: "दूरी", topSpeed: "टॉप स्पीड", sprints: "स्प्रिंट", touchMap: "टच मैप", attacking: "हमले की दिशा", moments: "पल", noMoments: "अभी कोई पल नहीं।", noTracking: "फ़िज़िकल डेटा के लिए ट्रैकिंग चाहिए।", onPitch: "मैदान पर", off: "बाहर" },
};

export function focusStrings(lang: string | null | undefined): FocusStrings {
  return FOCUS[isLang(lang) ? lang : "en"];
}
